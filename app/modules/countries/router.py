"""REST API for the University Requirement Reader (v3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.deps import CurrentAdmin, get_current_admin, require_super_admin
from app.modules.countries import chatbot, keystore, reader, repository as repo, scheduler, verify
from app.modules.countries.schemas import (
    ApiKeyIn, ApiKeyOut, BrokenLinkOut, ChangeOut, ChangeReview, ChatIn, ChatOut,
    CountryOut, LinkIn, LinkUpdate, OpResult, ProgramIn, ProgramOut, ProgramUpdate,
    ExploreIn, ExploreOut, ExploreSaveIn,
    ProgramWithUniversity, ReadUniversityOut, ReaderRunOut, ReaderRunResult, RunCompareOut,
    ScheduleIn, ScheduleOut, StatsOut, UniversityDetail, UniversityIn, UniversityOut,
    UniversityUpdate, VerifyExcelOut, VerifyReadUrlIn, VerifyReadUrlOut,
)

router = APIRouter(prefix="/api/v1", tags=["countries"])


def _scope(admin: CurrentAdmin) -> list[str] | None:
    """None = all countries (super-admin); else the admin's assigned codes."""
    return None if admin.is_super else admin.country_codes


def _assert_access(admin: CurrentAdmin, code: str | None):
    if not admin.may_access(code):
        raise HTTPException(403, "You are not assigned to this country.")


# =====================================================================
#  OVERVIEW
# =====================================================================
@router.get("/stats", response_model=StatsOut)
async def get_stats(admin: CurrentAdmin = Depends(get_current_admin),
                    db: AsyncSession = Depends(get_db)):
    return await repo.stats(db, scope=_scope(admin))


@router.post("/chat", response_model=ChatOut)
async def chat(payload: ChatIn, admin: CurrentAdmin = Depends(get_current_admin),
               db: AsyncSession = Depends(get_db)):
    return await chatbot.answer(db, payload.message)


@router.get("/countries", response_model=list[CountryOut])
async def countries(admin: CurrentAdmin = Depends(get_current_admin),
                    db: AsyncSession = Depends(get_db)):
    return await repo.list_countries(db, scope=_scope(admin))


@router.get("/countries/{code}/universities", response_model=list[UniversityOut])
async def universities(code: str, admin: CurrentAdmin = Depends(get_current_admin),
                       db: AsyncSession = Depends(get_db)):
    _assert_access(admin, code.upper())
    if not await repo.get_country(db, code):
        raise HTTPException(404, f"Country '{code}' not found")
    return await repo.list_universities(db, country_code=code, scope=_scope(admin))


@router.get("/universities", response_model=list[UniversityOut])
async def all_universities(country: str | None = None, q: str | None = None,
                           admin: CurrentAdmin = Depends(get_current_admin),
                           db: AsyncSession = Depends(get_db)):
    return await repo.list_universities(db, country_code=country, q=q, scope=_scope(admin))


@router.get("/universities/{university_id}", response_model=UniversityDetail)
async def university_detail(university_id: str, admin: CurrentAdmin = Depends(get_current_admin),
                            db: AsyncSession = Depends(get_db)):
    u = await repo.get_university(db, university_id)
    if not u:
        raise HTTPException(404, "University not found")
    _assert_access(admin, u.country.code if u.country else None)
    m = sum(1 for p in u.programs if p.degree_level == "MASTER")
    b = sum(1 for p in u.programs if p.degree_level == "BACHELOR")
    return UniversityDetail(
        id=u.id, name=u.name, slug=u.slug, city=u.city, type=u.type, website=u.website,
        contact=u.contact, origin=u.origin, program_count=len(u.programs),
        master_count=m, bachelor_count=b,
        country_code=u.country.code if u.country else None,
        country_name=u.country.name if u.country else None,
        programs=[ProgramOut.model_validate(p) for p in u.programs])


@router.get("/programs/{program_id}", response_model=ProgramOut)
async def program_detail(program_id: str, admin: CurrentAdmin = Depends(get_current_admin),
                         db: AsyncSession = Depends(get_db)):
    p = await repo.get_program(db, program_id)
    if not p:
        raise HTTPException(404, "Program not found")
    return p


@router.get("/programs", response_model=list[ProgramWithUniversity])
async def programs(
    country: str | None = Query(None, description="country code e.g. FR, IT"),
    degree: str | None = Query(None, description="MASTER / BACHELOR"),
    q: str | None = Query(None, description="search programme or university name"),
    status: str | None = Query(None, description="OPEN / CLOSE"),
    has_link: bool | None = Query(None),
    limit: int = Query(200, le=2000),
    offset: int = 0,
    admin: CurrentAdmin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return await repo.search_programs(db, country_code=country, degree=degree, q=q,
                                      status=status, has_link=has_link, limit=limit,
                                      offset=offset, scope=_scope(admin))


@router.get("/links")
async def links(country: str | None = None, only_broken: bool = False,
                admin: CurrentAdmin = Depends(get_current_admin),
                db: AsyncSession = Depends(get_db)):
    return await repo.list_links(db, country_code=country, only_broken=only_broken,
                                 scope=_scope(admin))


@router.get("/links/broken", response_model=list[BrokenLinkOut])
async def broken_links(country: str | None = Query(None),
                       admin: CurrentAdmin = Depends(get_current_admin),
                       db: AsyncSession = Depends(get_db)):
    return await repo.broken_links(db, country_code=country, scope=_scope(admin))


# =====================================================================
#  READER
# =====================================================================
@router.post("/reader/run", response_model=ReaderRunResult)
async def reader_run(
    countries: str | None = Query(None, description="comma-separated codes, e.g. 'FR' or 'FR,IT'"),
    use_ai: bool | None = Query(None, description="force AI on/off; default = AI if a key is set"),
    max_pages: int | None = Query(None, description="cap pages read (safety for big runs)"),
    discover: bool = Query(False, description="auto-find a link for programmes that have none"),
    admin: CurrentAdmin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Start a background read of the chosen countries' links. Poll GET /reader/runs."""
    codes = [c for c in (countries.split(",") if countries else []) if c.strip()]
    if not admin.is_super:
        codes = admin.country_codes      # country-admins can only read their own countries
    return await reader.start_background_read(db, countries=codes or None,
                                              use_ai=use_ai, max_pages=max_pages,
                                              discover=discover)


@router.get("/reader/estimate")
async def reader_estimate(countries: str | None = Query(None),
                          university_id: str | None = Query(None),
                          admin: CurrentAdmin = Depends(get_current_admin),
                          db: AsyncSession = Depends(get_db)):
    """Estimate how long a read will take before starting it."""
    code = (countries.split(",")[0].strip() if countries else None) or None
    return await repo.reader_estimate(db, country_code=code, university_id=university_id,
                                      scope=_scope(admin))


@router.get("/reader/runs", response_model=list[ReaderRunOut])
async def reader_runs(limit: int = Query(20, le=100),
                      admin: CurrentAdmin = Depends(get_current_admin),
                      db: AsyncSession = Depends(get_db)):
    return await repo.list_runs(db, limit=limit)


@router.post("/universities/{university_id}/read", response_model=ReadUniversityOut)
async def read_university(university_id: str, admin: CurrentAdmin = Depends(get_current_admin),
                         db: AsyncSession = Depends(get_db)):
    """Read every link of ONE university right now and return stored-vs-read at once.
    Fast proof that the reader works on a freshly-added university."""
    u = await repo.get_university(db, university_id)
    if not u:
        raise HTTPException(404, "University not found")
    _assert_access(admin, u.country.code if u.country else None)
    return await repo.read_university_now(db, university_id, scope=_scope(admin))


@router.get("/reader/runs/{run_id}/compare", response_model=RunCompareOut)
async def reader_run_compare(run_id: str, only_changed: bool = Query(False),
                             admin: CurrentAdmin = Depends(get_current_admin),
                             db: AsyncSession = Depends(get_db)):
    """Side-by-side: what the reader read vs what is stored, for this run."""
    return await repo.run_compare(db, run_id, only_changed=only_changed, scope=_scope(admin))


# backward-compatible alias
@router.post("/links/check", response_model=ReaderRunResult)
async def links_check(country: str | None = Query(None),
                      admin: CurrentAdmin = Depends(get_current_admin),
                      db: AsyncSession = Depends(get_db)):
    codes = [country] if country else None
    if not admin.is_super:
        codes = admin.country_codes
    return await reader.start_background_read(db, countries=codes)


# =====================================================================
#  CHANGES
# =====================================================================
@router.get("/changes", response_model=list[ChangeOut])
async def changes(run: str | None = Query(None), country: str | None = Query(None),
                  status: str = Query("PENDING", description="PENDING / APPROVED / DECLINED / ALL"),
                  admin: CurrentAdmin = Depends(get_current_admin),
                  db: AsyncSession = Depends(get_db)):
    return await repo.list_changes(db, run_id=run, country_code=country, status=status,
                                   scope=_scope(admin))


@router.post("/changes/{change_id}/review", response_model=OpResult)
async def review_change(change_id: str, payload: ChangeReview,
                        admin: CurrentAdmin = Depends(get_current_admin),
                        db: AsyncSession = Depends(get_db)):
    return await repo.review_change(db, change_id, payload.decision)


@router.delete("/changes/{change_id}", response_model=OpResult)
async def remove_change(change_id: str, admin: CurrentAdmin = Depends(get_current_admin),
                        db: AsyncSession = Depends(get_db)):
    """Remove a change from the list (e.g. an approved/declined one you're done with)."""
    return await repo.delete_change(db, change_id)


# =====================================================================
#  SCHEDULE
# =====================================================================
@router.get("/schedule", response_model=ScheduleOut)
async def get_schedule(admin: CurrentAdmin = Depends(get_current_admin),
                       db: AsyncSession = Depends(get_db)):
    return await repo.get_schedule(db)


@router.put("/schedule", response_model=ScheduleOut)
async def set_schedule(payload: ScheduleIn, _: CurrentAdmin = Depends(require_super_admin),
                       db: AsyncSession = Depends(get_db)):
    await repo.update_schedule(db, payload.model_dump(exclude_unset=True))
    await scheduler.reschedule()
    return await repo.get_schedule(db)


# =====================================================================
#  API-KEY STORE  (super-admin only — keys are system-wide secrets)
# =====================================================================
@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(_: CurrentAdmin = Depends(require_super_admin),
                        db: AsyncSession = Depends(get_db)):
    return await keystore.list_keys(db)


@router.post("/api-keys", response_model=OpResult)
async def add_api_key(payload: ApiKeyIn, admin: CurrentAdmin = Depends(require_super_admin),
                      db: AsyncSession = Depends(get_db)):
    res = await keystore.add_key(db, payload.provider, payload.key, payload.label,
                                 payload.model, created_by=admin.email)
    return OpResult(ok=True, message=res["message"])


@router.post("/api-keys/{key_id}/activate", response_model=OpResult)
async def activate_api_key(key_id: str, _: CurrentAdmin = Depends(require_super_admin),
                           db: AsyncSession = Depends(get_db)):
    res = await keystore.set_active(db, key_id)
    return OpResult(ok=True, message=res["message"])


@router.delete("/api-keys/{key_id}", response_model=OpResult)
async def delete_api_key(key_id: str, _: CurrentAdmin = Depends(require_super_admin),
                         db: AsyncSession = Depends(get_db)):
    res = await keystore.delete_key(db, key_id)
    return OpResult(ok=True, message=res["message"])


# =====================================================================
#  DATA ENTRY / EDITING
# =====================================================================
@router.post("/universities", response_model=OpResult)
async def create_university(payload: UniversityIn, admin: CurrentAdmin = Depends(get_current_admin),
                            db: AsyncSession = Depends(get_db)):
    _assert_access(admin, payload.country_code.upper())
    return await repo.create_university(db, payload.model_dump())


@router.put("/universities/{university_id}", response_model=OpResult)
async def edit_university(university_id: str, payload: UniversityUpdate,
                          admin: CurrentAdmin = Depends(get_current_admin),
                          db: AsyncSession = Depends(get_db)):
    return await repo.update_university(db, university_id, payload.model_dump(exclude_unset=True))


@router.delete("/universities/{university_id}", response_model=OpResult)
async def remove_university(university_id: str, admin: CurrentAdmin = Depends(get_current_admin),
                            db: AsyncSession = Depends(get_db)):
    return await repo.delete_university(db, university_id)


@router.post("/universities/{university_id}/programs", response_model=OpResult)
async def add_program(university_id: str, payload: ProgramIn,
                      admin: CurrentAdmin = Depends(get_current_admin),
                      db: AsyncSession = Depends(get_db)):
    return await repo.add_program_to_university(db, university_id, payload.model_dump())


@router.put("/programs/{program_id}", response_model=OpResult)
async def edit_program(program_id: str, payload: ProgramUpdate,
                       admin: CurrentAdmin = Depends(get_current_admin),
                       db: AsyncSession = Depends(get_db)):
    return await repo.update_program(db, program_id, payload.model_dump(exclude_unset=True))


@router.delete("/programs/{program_id}", response_model=OpResult)
async def remove_program(program_id: str, admin: CurrentAdmin = Depends(get_current_admin),
                         db: AsyncSession = Depends(get_db)):
    return await repo.delete_program(db, program_id)


@router.post("/links", response_model=OpResult)
async def create_link(payload: LinkIn, program_id: str | None = Query(None),
                      university_id: str | None = Query(None),
                      admin: CurrentAdmin = Depends(get_current_admin),
                      db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    data["program_id"] = program_id
    data["university_id"] = university_id
    return await repo.add_link(db, data)


@router.put("/links/{link_id}", response_model=OpResult)
async def edit_link(link_id: str, payload: LinkUpdate,
                    admin: CurrentAdmin = Depends(get_current_admin),
                    db: AsyncSession = Depends(get_db)):
    return await repo.update_link(db, link_id, payload.model_dump(exclude_unset=True))


@router.delete("/links/{link_id}", response_model=OpResult)
async def remove_link(link_id: str, admin: CurrentAdmin = Depends(get_current_admin),
                      db: AsyncSession = Depends(get_db)):
    return await repo.delete_link(db, link_id)


@router.post("/links/{link_id}/resolve", response_model=OpResult)
async def resolve_link(link_id: str, admin: CurrentAdmin = Depends(get_current_admin),
                       db: AsyncSession = Depends(get_db)):
    return await repo.resolve_link(db, link_id)


# =====================================================================
#  VERIFICATION
# =====================================================================
@router.get("/verify/excel", response_model=VerifyExcelOut)
async def verify_excel(_: CurrentAdmin = Depends(get_current_admin),
                       db: AsyncSession = Depends(get_db)):
    return await verify.verify_excel(db)


@router.post("/verify/explore", response_model=ExploreOut)
async def verify_explore(payload: ExploreIn, admin: CurrentAdmin = Depends(get_current_admin),
                         db: AsyncSession = Depends(get_db)):
    """Read a university/programme that may not be stored yet. If no link is given,
    the official page is found from the university name. Nothing is saved."""
    return await repo.explore_university(
        db, university=payload.university, country_code=payload.country_code,
        program=payload.program, degree_level=payload.degree_level,
        url=payload.url, scope=_scope(admin))


@router.post("/verify/explore/save", response_model=OpResult)
async def verify_explore_save(payload: ExploreSaveIn,
                              admin: CurrentAdmin = Depends(get_current_admin),
                              db: AsyncSession = Depends(get_db)):
    """Store the university + programme that was just read."""
    return await repo.save_explored(db, payload.model_dump(), scope=_scope(admin))


@router.post("/verify/read-url", response_model=VerifyReadUrlOut)
async def verify_read_url(payload: VerifyReadUrlIn, admin: CurrentAdmin = Depends(get_current_admin),
                          db: AsyncSession = Depends(get_db)):
    """Read a link now and compare the criteria it yields with a programme's stored data."""
    return await repo.read_and_compare(db, payload.url, payload.program_id)
