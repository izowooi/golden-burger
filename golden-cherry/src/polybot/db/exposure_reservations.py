"""Shared SQL contract for untracked live BUY exposure reservations."""

UNTRACKED_BUY_RESERVATIONS_SQL = """
    SELECT submission.submission_id, submission.order_id,
           submission.token_id, submission.requested_price,
           submission.requested_size, submission.submitted_at,
           UPPER(COALESCE(submission.response_status, '')) AS response_status,
           UPPER(COALESCE(submission.latest_order_status, '')) AS latest_order_status,
           submission.latest_size_matched, submission.needs_reconciliation,
           submission.outcome_resolution, submission.outcome_resolved_at,
           submission.outcome_resolution_reason
    FROM order_submissions AS submission
    WHERE submission.simulation = 0
      AND UPPER(submission.side) = 'BUY'
      AND NOT EXISTS (
          SELECT 1 FROM trades AS managed
          WHERE submission.order_id IS NOT NULL
            AND managed.buy_order_id = submission.order_id
      )
      AND NOT COALESCE((
          submission.outcome_resolution = 'NO_ORDER_CREATED'
          AND submission.order_id IS NULL
          AND submission.outcome_resolved_at IS NOT NULL
          AND NULLIF(TRIM(submission.outcome_resolution_reason), '') IS NOT NULL
      ), 0)
      AND NOT COALESCE((
          submission.order_id IS NULL
          AND UPPER(COALESCE(submission.response_status, '')) = 'FAILED'
      ), 0)
      AND NOT COALESCE((
          submission.order_id IS NOT NULL
          AND submission.needs_reconciliation = 0
          AND submission.latest_size_matched = 0
          AND UPPER(COALESCE(submission.latest_order_status, '')) IN (
              'CANCELED', 'CANCELLED', 'CANCELED_MARKET_RESOLVED', 'INVALID'
          )
      ), 0)
    ORDER BY submission.submitted_at, submission.submission_id
"""
