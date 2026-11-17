"""Pydantic schemas for the auth / admin module."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    role: str
    full_name: str | None = None
    countries: list[str] = []          # country codes the admin may manage ([] = all for super)


class CountryBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    code: str


class AdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime
    countries: list[CountryBrief] = []


class AdminCreateIn(BaseModel):
    email: str
    full_name: str | None = None
    password: str = Field(min_length=8)
    role: str = "COUNTRY_ADMIN"        # SUPER_ADMIN / COUNTRY_ADMIN
    country_codes: list[str] = []      # countries to assign (for COUNTRY_ADMIN)


class AdminUpdateIn(BaseModel):
    full_name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    is_active: bool | None = None
    country_codes: list[str] | None = None


class MeOut(BaseModel):
    email: str
    role: str
    full_name: str | None = None
    countries: list[str] = []
    is_super: bool = False
