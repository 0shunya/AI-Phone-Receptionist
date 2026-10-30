"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-30 21:37:15.583896
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


NO_OVERLAP = """
ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap
  EXCLUDE USING gist (
    staff_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  ) WHERE (status <> 'cancelled')
"""


def upgrade() -> None:
    # btree_gist lets a GiST index combine "same staff" (=) with "ranges overlap" (&&).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table('customers',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('phone_e164', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('phone_e164')
    )
    op.create_table('model_calls',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('trace_id', sa.String(length=64), nullable=False),
    sa.Column('call_id', sa.UUID(), nullable=True),
    sa.Column('build', sa.String(length=20), nullable=False),
    sa.Column('feature', sa.String(length=40), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('prompt_version', sa.String(length=40), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.Double(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('first_token_ms', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error_category', sa.String(length=40), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_model_calls_call_id'), 'model_calls', ['call_id'], unique=False)
    op.create_index(op.f('ix_model_calls_created_at'), 'model_calls', ['created_at'], unique=False)
    op.create_index(op.f('ix_model_calls_trace_id'), 'model_calls', ['trace_id'], unique=False)
    op.create_table('services',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('duration_min', sa.Integer(), nullable=False),
    sa.Column('price_minor', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('staff',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('calendar_id', sa.Text(), nullable=False),
    sa.Column('timezone', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('calls',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=True),
    sa.Column('provider_call_id', sa.Text(), nullable=True),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('caller_phone', sa.String(length=16), nullable=True),
    sa.Column('outcome', sa.Enum('in_progress', 'booked', 'rescheduled', 'cancelled', 'answered', 'handoff', 'abandoned', name='call_outcome'), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('recording_key', sa.Text(), nullable=True),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('session', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('handoff_reason', sa.Text(), nullable=True),
    sa.Column('handoff_status', sa.Enum('waiting', 'claimed', 'resolved', name='handoff_status'), nullable=True),
    sa.Column('brain', sa.String(length=60), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_calls_customer_id'), 'calls', ['customer_id'], unique=False)
    op.create_table('staff_shifts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('staff_id', sa.UUID(), nullable=False),
    sa.Column('weekday', sa.SmallInteger(), nullable=False),
    sa.Column('start_local', sa.Time(), nullable=False),
    sa.Column('end_local', sa.Time(), nullable=False),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_staff_shifts_staff_id'), 'staff_shifts', ['staff_id'], unique=False)
    op.create_table('bookings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('staff_id', sa.UUID(), nullable=False),
    sa.Column('service_id', sa.UUID(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Enum('confirmed', 'cancelled', name='booking_status'), nullable=False),
    sa.Column('event_id', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=200), nullable=True),
    sa.Column('call_id', sa.UUID(), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['service_id'], ['services.id'], ),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_bookings_call_id'), 'bookings', ['call_id'], unique=False)
    op.create_index(op.f('ix_bookings_customer_id'), 'bookings', ['customer_id'], unique=False)
    op.create_index('ix_bookings_staff_starts', 'bookings', ['staff_id', 'starts_at'], unique=False)
    # THE CALENDAR IS THE TRUTH: Postgres itself refuses a second live booking that overlaps the first.
    op.execute(NO_OVERLAP)
    op.create_table('tool_calls',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('call_id', sa.UUID(), nullable=False),
    sa.Column('turn_seq', sa.Integer(), nullable=True),
    sa.Column('tool', sa.String(length=40), nullable=False),
    sa.Column('args', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('error_code', sa.String(length=40), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('state', sa.String(length=20), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tool_calls_call_id'), 'tool_calls', ['call_id'], unique=False)
    op.create_table('turns',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('call_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('speaker', sa.Enum('caller', 'agent', name='speaker'), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=True),
    sa.Column('eos_to_audio_ms', sa.Integer(), nullable=True),
    sa.Column('server_first_ms', sa.Integer(), nullable=True),
    sa.Column('server_total_ms', sa.Integer(), nullable=True),
    sa.Column('interrupted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('call_id', 'seq', name='uq_turns_call_seq')
    )
    op.create_index(op.f('ix_turns_call_id'), 'turns', ['call_id'], unique=False)
    op.create_table('sms_messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('to_e164', sa.String(length=16), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('booking_id', sa.UUID(), nullable=True),
    sa.Column('call_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ),
    sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sms_messages_booking_id'), 'sms_messages', ['booking_id'], unique=False)
    op.create_index(op.f('ix_sms_messages_call_id'), 'sms_messages', ['call_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sms_messages_call_id'), table_name='sms_messages')
    op.drop_index(op.f('ix_sms_messages_booking_id'), table_name='sms_messages')
    op.drop_table('sms_messages')
    op.drop_index(op.f('ix_turns_call_id'), table_name='turns')
    op.drop_table('turns')
    op.drop_index(op.f('ix_tool_calls_call_id'), table_name='tool_calls')
    op.drop_table('tool_calls')
    op.drop_index('ix_bookings_staff_starts', table_name='bookings')
    op.drop_index(op.f('ix_bookings_customer_id'), table_name='bookings')
    op.drop_index(op.f('ix_bookings_call_id'), table_name='bookings')
    op.drop_table('bookings')
    op.drop_index(op.f('ix_staff_shifts_staff_id'), table_name='staff_shifts')
    op.drop_table('staff_shifts')
    op.drop_index(op.f('ix_calls_customer_id'), table_name='calls')
    op.drop_table('calls')
    op.drop_table('staff')
    op.drop_table('services')
    op.drop_index(op.f('ix_model_calls_trace_id'), table_name='model_calls')
    op.drop_index(op.f('ix_model_calls_created_at'), table_name='model_calls')
    op.drop_index(op.f('ix_model_calls_call_id'), table_name='model_calls')
    op.drop_table('model_calls')
    op.drop_table('customers')
    for enum_name in ("booking_status", "call_outcome", "handoff_status", "speaker"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
