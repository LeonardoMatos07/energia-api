from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONDOC = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
STATUSES = "('published', 'hidden', 'removed_on_request')"

CC_BY_ND = "https://creativecommons.org/licenses/by-nd/3.0/deed.pt_BR"
CC_BY = "https://creativecommons.org/licenses/by/4.0/deed.pt_BR"

SOURCES = [
    {
        "slug": "aneel",
        "publisher_name": "Agência Nacional de Energia Elétrica (ANEEL)",
        "homepage_url": "https://www.gov.br/aneel/pt-br/assuntos/noticias",
        "feed_url": "https://www.gov.br/aneel/++api++/pt-br/@search",
        "fetch_method": "api",
        "license_code": "CC-BY-ND-3.0",
        "license_url": CC_BY_ND,
        "license_evidence_url": "https://www.gov.br/aneel/pt-br/assuntos/noticias",
        "active": True,
    },
    {
        "slug": "mme",
        "publisher_name": "Ministério de Minas e Energia (MME)",
        "homepage_url": "https://www.gov.br/mme/pt-br/assuntos/noticias",
        "feed_url": "https://www.gov.br/mme/pt-br/assuntos/noticias",
        "fetch_method": "listing",
        "license_code": "CC-BY-ND-3.0",
        "license_url": CC_BY_ND,
        "license_evidence_url": "https://www.gov.br/mme/pt-br",
        "active": True,
    },
    {
        "slug": "anp",
        "publisher_name": "Agência Nacional do Petróleo, Gás Natural e Biocombustíveis (ANP)",
        "homepage_url": "https://www.gov.br/anp/pt-br/canais_atendimento/imprensa/noticias-comunicados",
        "feed_url": "https://www.gov.br/anp/pt-br/canais_atendimento/imprensa/noticias-comunicados",
        "fetch_method": "listing",
        "license_code": "CC-BY-ND-3.0",
        "license_url": CC_BY_ND,
        "license_evidence_url": "https://www.gov.br/anp/pt-br",
        "active": True,
    },
    {
        "slug": "epe",
        "publisher_name": "Empresa de Pesquisa Energética (EPE)",
        "homepage_url": "https://www.epe.gov.br/pt/imprensa/noticias",
        "feed_url": "https://www.epe.gov.br/pt/imprensa/noticias",
        "fetch_method": "listing",
        "license_code": "CC-BY-4.0",
        "license_url": CC_BY,
        "license_evidence_url": "https://www.epe.gov.br/pt/imprensa/noticias",
        "active": True,
    },
    {
        "slug": "dou",
        "publisher_name": "Diário Oficial da União (Imprensa Nacional)",
        "homepage_url": "https://www.in.gov.br/",
        "feed_url": None,
        "fetch_method": "open-data",
        "license_code": "public-domain-official-act",
        "license_url": "https://www.planalto.gov.br/ccivil_03/leis/l9610.htm",
        "license_evidence_url": "https://www.planalto.gov.br/ccivil_03/leis/l9610.htm",
        "active": False,
    },
]


def upgrade() -> None:
    news_source = op.create_table(
        "news_source",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("publisher_name", sa.Text(), nullable=False),
        sa.Column("homepage_url", sa.Text(), nullable=False),
        sa.Column("feed_url", sa.Text(), nullable=True),
        sa.Column("fetch_method", sa.Text(), nullable=False),
        sa.Column("license_code", sa.Text(), nullable=False),
        sa.Column("license_url", sa.Text(), nullable=False),
        sa.Column("license_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("license_evidence_url", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "fetch_method IN ('rss', 'api', 'listing', 'open-data')",
            name="ck_news_source_fetch_method",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_news_source"),
        sa.UniqueConstraint("slug", name="uq_news_source_slug"),
    )
    op.create_table(
        "news_item",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("topics", JSONDOC, nullable=False),
        sa.Column("status", sa.Text(), server_default="published", nullable=False),
        sa.Column("editor_note", sa.Text(), nullable=True),
        sa.Column("editor_note_by", sa.Text(), nullable=True),
        sa.Column("editor_note_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("related_links", JSONDOC, nullable=True),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_news_item_status"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["news_source.id"], name="fk_news_item_source_id_news_source"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_news_item"),
        sa.UniqueConstraint("url", name="uq_news_item_url"),
    )
    op.create_index("ix_news_item_published_at", "news_item", ["published_at", "id"])
    op.create_index("ix_news_item_source_id", "news_item", ["source_id"])
    op.create_table(
        "regulatory_act",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("organ", sa.Text(), nullable=False),
        sa.Column("act_type", sa.Text(), nullable=False),
        sa.Column("act_number", sa.Text(), nullable=True),
        sa.Column("act_date", sa.Date(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("dou_date", sa.Date(), nullable=False),
        sa.Column("dou_section", sa.SmallInteger(), nullable=True),
        sa.Column("dou_edition", sa.Text(), nullable=True),
        sa.Column("dou_page", sa.Text(), nullable=True),
        sa.Column("dou_url", sa.Text(), nullable=False),
        sa.Column("topics", JSONDOC, nullable=False),
        sa.Column("status", sa.Text(), server_default="published", nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_regulatory_act_status"),
        sa.PrimaryKeyConstraint("id", name="pk_regulatory_act"),
        sa.UniqueConstraint("dou_url", "content_hash", name="uq_regulatory_act_dou_url_hash"),
    )
    op.create_index("ix_regulatory_act_dou_date", "regulatory_act", ["dou_date", "id"])
    op.bulk_insert(news_source, SOURCES)


def downgrade() -> None:
    op.drop_table("regulatory_act")
    op.drop_table("news_item")
    op.drop_table("news_source")
