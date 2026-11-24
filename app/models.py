import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc)


spot_access_groups = Table(
    "spot_access_groups",
    Base.metadata,
    Column("spot_id", String, ForeignKey("spots.id"), primary_key=True),
    Column("access_group_id", String, ForeignKey("access_groups.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String(120), nullable=False)
    email = Column(String(254), nullable=False, unique=True, index=True)
    password_hash = Column(String(300), nullable=False)
    role = Column(String(30), nullable=False, default="public")  # public, data_entry, admin
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    ratings = relationship("Rating", back_populates="user")

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def can_input(self):
        return self.role in {"data_entry", "admin"}


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=new_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    user = relationship("User", back_populates="sessions")


class AnonymousSubject(Base):
    __tablename__ = "anonymous_subjects"

    id = Column(String, primary_key=True, default=new_id)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    ratings = relationship("Rating", back_populates="anonymous_subject")


class AccessGroup(Base):
    __tablename__ = "access_groups"

    id = Column(String, primary_key=True, default=new_id)
    code = Column(String(50), nullable=False, unique=True)
    label = Column(String(100), nullable=False)
    is_private = Column(Boolean, nullable=False, default=False)

    spots = relationship("Spot", secondary=spot_access_groups, back_populates="access_groups")


class Venue(Base):
    __tablename__ = "venues"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String(200), nullable=False)
    slug = Column(String(220), nullable=False, unique=True, index=True)
    category = Column(String(50), nullable=False)
    address = Column(String(400), nullable=False)
    admin_city = Column(String(80), nullable=False)
    district = Column(String(100), nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    opening_hours = Column(String(300), nullable=True)
    is_published = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    spots = relationship("Spot", back_populates="venue", cascade="all, delete-orphan")


class Spot(Base):
    __tablename__ = "spots"
    __table_args__ = (UniqueConstraint("venue_id", "slug", name="uq_spot_venue_slug"),)

    id = Column(String, primary_key=True, default=new_id)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(220), nullable=False)
    floor = Column(String(50), nullable=False)
    zone_landmark = Column(String(300), nullable=False)
    recommended_entrance = Column(String(150), nullable=True)
    walking_directions = Column(Text, nullable=True)
    access_notes = Column(String(300), nullable=True)
    existence_status = Column(String(20), nullable=False, default="ada")
    is_published = Column(Boolean, nullable=False, default=True)
    last_verified_at = Column(Date, nullable=True)
    verification_method = Column(String(50), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    venue = relationship("Venue", back_populates="spots")
    placements = relationship("Placement", back_populates="spot", cascade="all, delete-orphan")
    access_groups = relationship("AccessGroup", secondary=spot_access_groups, back_populates="spots")
    photos = relationship("Photo", back_populates="spot", cascade="all, delete-orphan")
    public_events = relationship("PublicChangeEvent", back_populates="spot", cascade="all, delete-orphan")
    verification_logs = relationship("VerificationLog", back_populates="spot", cascade="all, delete-orphan")

    @property
    def access_codes(self):
        return [group.code for group in self.access_groups]

    @property
    def access_labels(self):
        return [group.label for group in self.access_groups]

    @property
    def is_private_only(self):
        return bool(self.access_groups) and all(group.is_private for group in self.access_groups)


class Brand(Base):
    __tablename__ = "brands"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String(120), nullable=False, unique=True)
    slug = Column(String(140), nullable=False, unique=True)
    notes = Column(Text, nullable=True)

    chair_types = relationship("ChairType", back_populates="brand", cascade="all, delete-orphan")
    placements = relationship("Placement", back_populates="brand")


class Operator(Base):
    __tablename__ = "operators"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String(150), nullable=False, unique=True)

    placements = relationship("Placement", back_populates="operator")


class BodyArea(Base):
    __tablename__ = "body_areas"

    id = Column(String, primary_key=True, default=new_id)
    code = Column(String(50), nullable=False, unique=True)
    label = Column(String(100), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    type_mappings = relationship("ChairTypeBodyArea", back_populates="body_area")


class ChairType(Base):
    __tablename__ = "chair_types"
    __table_args__ = (UniqueConstraint("brand_id", "name", name="uq_brand_type_name"),)

    id = Column(String, primary_key=True, default=new_id)
    brand_id = Column(String, ForeignKey("brands.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    slug = Column(String(170), nullable=False)
    notes = Column(Text, nullable=True)

    brand = relationship("Brand", back_populates="chair_types")
    body_areas = relationship("ChairTypeBodyArea", back_populates="chair_type", cascade="all, delete-orphan")
    placements = relationship("Placement", back_populates="chair_type")

    @property
    def supported_areas(self):
        mappings = [m for m in self.body_areas if m.support_status == "didukung"]
        return sorted(mappings, key=lambda m: m.body_area.sort_order if m.body_area else 0)


class ChairTypeBodyArea(Base):
    __tablename__ = "chair_type_body_areas"
    __table_args__ = (
        UniqueConstraint("chair_type_id", "body_area_id", name="uq_type_body_area"),
    )

    id = Column(String, primary_key=True, default=new_id)
    chair_type_id = Column(String, ForeignKey("chair_types.id"), nullable=False, index=True)
    body_area_id = Column(String, ForeignKey("body_areas.id"), nullable=False, index=True)
    support_status = Column(String(30), nullable=False, default="belum_dikonfirmasi")
    mechanism = Column(String(50), nullable=True)
    source_note = Column(String(300), nullable=True)
    confirmed_at = Column(Date, nullable=True)

    chair_type = relationship("ChairType", back_populates="body_areas")
    body_area = relationship("BodyArea", back_populates="type_mappings")


class Placement(Base):
    __tablename__ = "placements"

    id = Column(String, primary_key=True, default=new_id)
    spot_id = Column(String, ForeignKey("spots.id"), nullable=False, index=True)
    brand_id = Column(String, ForeignKey("brands.id"), nullable=True)
    chair_type_id = Column(String, ForeignKey("chair_types.id"), nullable=True)
    operator_id = Column(String, ForeignKey("operators.id"), nullable=True)
    quantity = Column(Integer, nullable=True)
    quantity_basis = Column(String(50), nullable=True)
    cost_type = Column(String(40), nullable=False, default="belum_diketahui")
    tariff_type = Column(String(40), nullable=True)
    price_idr = Column(Integer, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    price_is_starting_from = Column(Boolean, nullable=False, default=False)
    payment_methods = Column(String(300), nullable=True)  # semicolon-separated codes for MVP
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    is_published = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    spot = relationship("Spot", back_populates="placements")
    brand = relationship("Brand", back_populates="placements")
    chair_type = relationship("ChairType", back_populates="placements")
    operator = relationship("Operator", back_populates="placements")
    ratings = relationship("Rating", back_populates="placement", cascade="all, delete-orphan")

    @property
    def is_active(self):
        return self.effective_to is None

    @property
    def payment_codes(self):
        return [x for x in (self.payment_methods or "").split(";") if x]

    @property
    def can_rate(self):
        return bool(self.brand_id and self.chair_type_id and self.chair_type and self.chair_type.supported_areas)


class Photo(Base):
    __tablename__ = "photos"

    id = Column(String, primary_key=True, default=new_id)
    spot_id = Column(String, ForeignKey("spots.id"), nullable=False, index=True)
    storage_name = Column(String(300), nullable=False)
    original_name = Column(String(300), nullable=True)
    intended_use = Column(String(40), nullable=False, default="bukti_internal")
    caption = Column(String(300), nullable=True)
    license_note = Column(String(400), nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    uploaded_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    spot = relationship("Spot", back_populates="photos")


class VerificationLog(Base):
    __tablename__ = "verification_logs"

    id = Column(String, primary_key=True, default=new_id)
    spot_id = Column(String, ForeignKey("spots.id"), nullable=True, index=True)
    placement_id = Column(String, ForeignKey("placements.id"), nullable=True)
    checked_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    source_title = Column(String(300), nullable=True)
    source_url = Column(String(600), nullable=True)
    source_type = Column(String(60), nullable=True)
    source_published_date = Column(Date, nullable=True)
    checked_date = Column(Date, nullable=False, default=date.today)
    check_method = Column(String(50), nullable=False)
    confidence = Column(String(20), nullable=False)
    fields_supported = Column(String(400), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    spot = relationship("Spot", back_populates="verification_logs")


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    __table_args__ = (Index("ix_change_requests_status", "status"),)

    id = Column(String, primary_key=True, default=new_id)
    submitter_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    reviewer_id = Column(String, ForeignKey("users.id"), nullable=True)
    kind = Column(String(50), nullable=False)  # create_location, spot_update, spot_status
    target_spot_id = Column(String, ForeignKey("spots.id"), nullable=True)
    base_version = Column(Integer, nullable=True)
    payload_json = Column(Text, nullable=False)
    staged_photo_name = Column(String(300), nullable=True)
    status = Column(String(30), nullable=False, default="submitted")
    change_kind = Column(String(30), nullable=True)  # lapangan, koreksi
    review_note = Column(Text, nullable=True)
    applied_spot_id = Column(String, ForeignKey("spots.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    submitter = relationship("User", foreign_keys=[submitter_id])
    reviewer = relationship("User", foreign_keys=[reviewer_id])
    target_spot = relationship("Spot", foreign_keys=[target_spot_id])
    applied_spot = relationship("Spot", foreign_keys=[applied_spot_id])


class PublicChangeEvent(Base):
    __tablename__ = "public_change_events"

    id = Column(String, primary_key=True, default=new_id)
    spot_id = Column(String, ForeignKey("spots.id"), nullable=False, index=True)
    placement_id = Column(String, ForeignKey("placements.id"), nullable=True)
    event_type = Column(String(60), nullable=False)
    summary = Column(String(500), nullable=False)
    effective_date = Column(Date, nullable=False, default=date.today)
    change_request_id = Column(String, ForeignKey("change_requests.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    spot = relationship("Spot", back_populates="public_events")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=new_id)
    actor_id = Column(String, ForeignKey("users.id"), nullable=True)
    entity_type = Column(String(60), nullable=False)
    entity_id = Column(String, nullable=True)
    action = Column(String(80), nullable=False)
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("placement_id", "user_id", name="uq_rating_placement_user"),
        UniqueConstraint("placement_id", "anonymous_subject_id", name="uq_rating_placement_anon"),
    )

    id = Column(String, primary_key=True, default=new_id)
    placement_id = Column(String, ForeignKey("placements.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    anonymous_subject_id = Column(
        String, ForeignKey("anonymous_subjects.id"), nullable=True, index=True
    )
    active_version_id = Column(String, nullable=True)
    pending_version_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    placement = relationship("Placement", back_populates="ratings")
    user = relationship("User", back_populates="ratings")
    anonymous_subject = relationship("AnonymousSubject", back_populates="ratings")
    versions = relationship(
        "RatingVersion", back_populates="rating", cascade="all, delete-orphan", order_by="RatingVersion.version_no"
    )

    @property
    def active_version(self):
        return next((v for v in self.versions if v.id == self.active_version_id), None)

    @property
    def pending_version(self):
        return next((v for v in self.versions if v.id == self.pending_version_id), None)

    @property
    def subject_label(self):
        return self.user.name if self.user else "Anonim"


class RatingVersion(Base):
    __tablename__ = "rating_versions"
    __table_args__ = (
        UniqueConstraint("rating_id", "version_no", name="uq_rating_version_no"),
        Index("ix_rating_versions_status", "status"),
    )

    id = Column(String, primary_key=True, default=new_id)
    rating_id = Column(String, ForeignKey("ratings.id"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    usage_date = Column(Date, nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    reviewed_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    rating = relationship("Rating", back_populates="versions")
    scores = relationship("RatingAreaScore", back_populates="version", cascade="all, delete-orphan")
    reviewed_by = relationship("User")


class RatingAreaScore(Base):
    __tablename__ = "rating_area_scores"
    __table_args__ = (
        UniqueConstraint("rating_version_id", "body_area_id", name="uq_version_area"),
    )

    id = Column(String, primary_key=True, default=new_id)
    rating_version_id = Column(String, ForeignKey("rating_versions.id"), nullable=False, index=True)
    body_area_id = Column(String, ForeignKey("body_areas.id"), nullable=False, index=True)
    stars = Column(Integer, nullable=False)

    version = relationship("RatingVersion", back_populates="scores")
    body_area = relationship("BodyArea")
