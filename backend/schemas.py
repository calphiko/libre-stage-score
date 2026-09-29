from datetime import time
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class UserGroup(str, Enum):
    admin = "admin"
    editor = "editor"
    user = "user"


class AccessScope(str, Enum):
    user = "user"
    group = "group"
    public = "public"


class UserStatus(str, Enum):
    active = "active"
    deactivated = "deactivated"


class UserCreate(BaseModel):
    user_name: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$")
    clear_name: str
    email: EmailStr
    user_pw: str = Field(..., min_length=8, max_length=128)
    user_group: UserGroup
    status: UserStatus = UserStatus.active


class GroupOut(BaseModel):
    id: int
    name: str
    notes: str | None = None

    model_config = {"from_attributes": True}


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    notes: str | None = None


class GroupUpdate(GroupCreate):
    pass


class UserGroupMembershipUpdate(BaseModel):
    group_ids: list[int] = Field(default_factory=list)


class GroupInstrumentMembershipUpdate(BaseModel):
    instrument_ids: list[int] = Field(default_factory=list)
    notes: str | None = None


class UserOut(BaseModel):
    id: int
    user_name: str
    user_group: UserGroup
    email: EmailStr
    clear_name: str
    groups: list[GroupOut] = Field(default_factory=list)

    status: UserStatus = UserStatus.active

    model_config = {"from_attributes": True}


class UserListElem(BaseModel):
    id: int
    user_name: str
    clear_name: str
    is_singer: bool

    model_config = {"from_attributes": True}


class InstrumentCreate(BaseModel):
    instrument_name: str = Field(..., min_length=1, max_length=128)
    instrument_tuning: str = Field(..., min_length=1, max_length=32)
    notes: str | None = None


class InstrumentUpdate(InstrumentCreate):
    pass


class InstrumentOut(InstrumentCreate):
    id: int

    model_config = {"from_attributes": True}


class AssignInstrumentRequest(BaseModel):
    instrument_ids: list[int] = Field(default_factory=list)
    notes: str | None = None


class UserInstrumentOut(BaseModel):
    id: int
    user_id: int
    instrument_id: int
    instrument_name: str | None = None
    instrument_tuning: str | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class GroupInstrumentOut(BaseModel):
    id: int
    group_id: int
    instrument_id: int
    instrument_name: str | None = None
    instrument_tuning: str | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class CollectionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    notes: str | None = None


class CollectionCreate(CollectionBase):
    pass


class CollectionUpdate(CollectionBase):
    pass


class CollectionOut(CollectionBase):
    id: int

    model_config = {"from_attributes": True}


class SongCollectionsUpdate(BaseModel):
    collection_ids: list[int] = Field(default_factory=list)


class SongBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)
    tune: str | None = Field(default=None, max_length=32)
    composer: str | None = Field(default=None, max_length=1024)
    arrangement: str | None = Field(default=None, max_length=1024)
    length: time | None = None
    notes: str | None = None


class SongCreate(SongBase):
    pass


class SongUpdate(SongBase):
    pass


class SongOut(SongBase):
    id: int
    collections: list[CollectionOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ScoreBase(BaseModel):
    song_id: int
    instrument_id: int
    storage_path: str | None = Field(default=None, max_length=2048)
    notes: str | None = None


class ScoreCreate(ScoreBase):
    pass


class ScoreUpdate(ScoreBase):
    pass


class ScoreOut(ScoreBase):
    id: int
    file_hash: str | None = None
    song_name: str | None = None
    instrument_name: str | None = None
    instrument_tuning: str | None = None

    model_config = {"from_attributes": True}


class ScoreUploadChapterOut(BaseModel):
    chapter_index: int
    chapter_title: str
    original_chapter_title: str | None = None
    start_page: int
    end_page: int
    suggested_instrument_id: int | None = None
    suggested_instrument_name: str | None = None
    suggested_instrument_tuning: str | None = None


class ScoreUploadPreviewOut(BaseModel):
    upload_token: str
    source_filename: str
    page_count: int
    chapters: list[ScoreUploadChapterOut] = Field(default_factory=list)


class ScoreUploadMappingIn(BaseModel):
    chapter_index: int
    include: bool = True
    instrument_id: int | None = None
    create_instrument_name: str | None = Field(default=None, max_length=128)
    create_instrument_tuning: str | None = Field(default=None, max_length=32)


class ScoreUploadCommitRequest(BaseModel):
    song_id: int
    upload_token: str = Field(..., min_length=8, max_length=255)
    notes: str | None = None
    mappings: list[ScoreUploadMappingIn] = Field(default_factory=list)


class StorageStatusOut(BaseModel):
    has_changes: bool
    database_tree_hash: str
    filesystem_tree_hash: str
    database_file_count: int
    filesystem_file_count: int
    added_files: list[str] = Field(default_factory=list)
    removed_files: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    unmatched_files: list[str] = Field(default_factory=list)


class StorageRescrapeOut(StorageStatusOut):
    created_scores: int = 0
    updated_scores: int = 0
    removed_scores: int = 0


class UserSelfUpdate(BaseModel):
    clear_name: str | None = None
    email: EmailStr | None = None
    mm_username: str | None = None


class UserAdminUpdate(BaseModel):
    user_name: str
    clear_name: str
    email: EmailStr
    user_group: UserGroup
    musician: bool
    is_singer: bool
    mm_username: str | None = None
    status: UserStatus = UserStatus.active


class PasswordUpdateRequest(BaseModel):
    user_id: int
    old_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    csrf_token: str
    token_type: str = "bearer"
    expires_in: int
    message: str
