"""Assessment history baseline."""
from alembic import op
import sqlalchemy as sa
revision="0001_assessments"
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    op.create_table("assessments",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("ticker",sa.String(24),nullable=False),sa.Column("market",sa.String(8),nullable=False),sa.Column("horizon",sa.String(32),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("payload",sa.JSON(),nullable=False))
    op.create_index("ix_assessments_ticker","assessments",["ticker"])
    op.create_index("ix_assessments_created_at","assessments",["created_at"])
def downgrade():
    op.drop_index("ix_assessments_created_at",table_name="assessments")
    op.drop_index("ix_assessments_ticker",table_name="assessments")
    op.drop_table("assessments")
