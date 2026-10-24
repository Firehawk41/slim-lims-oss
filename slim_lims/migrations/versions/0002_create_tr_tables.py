"""create tr tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    counter = op.create_table('tr_number_counter',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('last_value', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tr_number_counter'))
    )
    op.bulk_insert(counter, [{"id": 1, "last_value": 0}])
    op.create_table('tr_submissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tr_number', sa.String(length=20), nullable=False),
    sa.Column('source', sa.Integer(), nullable=False),
    sa.Column('file_name', sa.String(length=260), nullable=False),
    sa.Column('status', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('location', sa.String(length=20), nullable=False),
    sa.Column('request_type', sa.Integer(), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('submitted_by_type', sa.Integer(), nullable=False),
    sa.Column('submitted_by_id', sa.String(length=64), nullable=False),
    sa.Column('submitted_by_display', sa.String(length=254), nullable=False),
    sa.Column('snapshot_name', sa.String(length=200), nullable=False),
    sa.Column('snapshot_address_1', sa.String(length=200), nullable=False),
    sa.Column('snapshot_address_2', sa.String(length=200), nullable=False),
    sa.Column('customer_contact', sa.String(length=200), nullable=False),
    sa.Column('customer_phone', sa.String(length=50), nullable=False),
    sa.Column('payment_method', sa.Integer(), nullable=False),
    sa.Column('po_number', sa.String(length=100), nullable=False),
    sa.Column('date_received', sa.Date(), nullable=True),
    sa.Column('received_by', sa.String(length=10), nullable=True),
    sa.Column('receipt_recorded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('receipt_recorded_by_type', sa.Integer(), nullable=True),
    sa.Column('receipt_recorded_by_id', sa.String(length=64), nullable=True),
    sa.Column('receipt_recorded_by_display', sa.String(length=254), nullable=True),
    sa.Column('service_date_override', sa.Date(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tr_submissions')),
    sa.UniqueConstraint('tr_number', name=op.f('uq_tr_submissions_tr_number'))
    )
    with op.batch_alter_table('tr_submissions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tr_submissions_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_tr_submissions_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_tr_submissions_submitted_at'), ['submitted_at'], unique=False)

    op.create_table('tr_samples',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('submission_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('sample_name', sa.String(length=100), nullable=False),
    sa.Column('chemical_id', sa.Integer(), nullable=True),
    sa.Column('chemical_name', sa.String(length=200), nullable=False),
    sa.Column('processing_time', sa.Integer(), nullable=False),
    sa.Column('requested_time', sa.Time(), nullable=True),
    sa.Column('additional_notes', sa.Text(), nullable=False),
    sa.Column('wafer_size', sa.Integer(), nullable=True),
    sa.Column('reporting_unit', sa.Integer(), nullable=True),
    sa.Column('water_package', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['submission_id'], ['tr_submissions.id'], name=op.f('fk_tr_samples_submission_id_tr_submissions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tr_samples')),
    sa.UniqueConstraint('submission_id', 'position', name=op.f('uq_tr_samples_submission_id'))
    )
    with op.batch_alter_table('tr_samples', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tr_samples_submission_id'), ['submission_id'], unique=False)

    op.create_table('tr_status_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('submission_id', sa.Integer(), nullable=False),
    sa.Column('from_status', sa.Integer(), nullable=True),
    sa.Column('to_status', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_type', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.String(length=64), nullable=False),
    sa.Column('actor_display', sa.String(length=254), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['submission_id'], ['tr_submissions.id'], name=op.f('fk_tr_status_events_submission_id_tr_submissions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tr_status_events'))
    )
    with op.batch_alter_table('tr_status_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tr_status_events_submission_id'), ['submission_id'], unique=False)

    op.create_table('tr_submission_emails',
    sa.Column('submission_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.CheckConstraint("kind IN ('results_to', 'results_cc', 'invoice_to', 'invoice_cc')", name=op.f('ck_tr_submission_emails_kind_valid')),
    sa.ForeignKeyConstraint(['submission_id'], ['tr_submissions.id'], name=op.f('fk_tr_submission_emails_submission_id_tr_submissions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('submission_id', 'kind', 'position', name=op.f('pk_tr_submission_emails'))
    )
    op.create_table('tr_sample_additional_elements',
    sa.Column('sample_id', sa.Integer(), nullable=False),
    sa.Column('element_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['sample_id'], ['tr_samples.id'], name=op.f('fk_tr_sample_additional_elements_sample_id_tr_samples'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('sample_id', 'element_id', name=op.f('pk_tr_sample_additional_elements'))
    )
    op.create_table('tr_sample_analyses',
    sa.Column('sample_id', sa.Integer(), nullable=False),
    sa.Column('analysis_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['sample_id'], ['tr_samples.id'], name=op.f('fk_tr_sample_analyses_sample_id_tr_samples'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('sample_id', 'analysis_id', name=op.f('pk_tr_sample_analyses'))
    )


def downgrade() -> None:
    op.drop_table('tr_sample_analyses')
    op.drop_table('tr_sample_additional_elements')
    op.drop_table('tr_submission_emails')
    with op.batch_alter_table('tr_status_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tr_status_events_submission_id'))

    op.drop_table('tr_status_events')
    with op.batch_alter_table('tr_samples', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tr_samples_submission_id'))

    op.drop_table('tr_samples')
    with op.batch_alter_table('tr_submissions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tr_submissions_submitted_at'))
        batch_op.drop_index(batch_op.f('ix_tr_submissions_status'))
        batch_op.drop_index(batch_op.f('ix_tr_submissions_customer_id'))

    op.drop_table('tr_submissions')
    op.drop_table('tr_number_counter')
