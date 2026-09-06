"""Pre-POST SELL identity and exact-ID recovery, without replaying a POST.

The hash is a lookup candidate, never acceptance/fill/zero-fill evidence.
Old intents without original signed identity remain unresolved. No signatures,
wallet addresses, private keys or auth headers are persisted in this table.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import re
import sqlite3
from typing import Mapping

from polybot_observability import ExecutionLedger, SubmissionEvidenceError


class SellPostOutcomeUnknown(SubmissionEvidenceError):
    # The shared ledger deliberately treats an absent/unknown HTTP result as
    # uncertain. Do not let a generic decode/DB exception become FAILED=0 fill.
    status_code = None


def account_fingerprint(chain_id, funder, signature_type):
    return hashlib.sha256(f'{int(chain_id)}:{str(funder).lower()}:{int(signature_type)}'.encode()).hexdigest()


def signed_sell_identity(signed, *, chain_id, neg_risk, funder, signature_type):
    from py_clob_client_v2.config import get_contract_config
    from py_clob_client_v2.order_utils.exchange_order_builder_v2 import ExchangeOrderBuilderV2
    if type(neg_risk) is not bool or int(signed.side) != 1:
        raise ValueError("SELL identity requires exact side/domain")
    if (int(signed.signatureType) != int(signature_type)
            or str(signed.maker).lower() != str(funder).lower()):
        raise ValueError("SELL identity account mismatch")
    if int(signature_type) == 3 and str(signed.signer).lower() != str(signed.maker).lower():
        raise ValueError("POLY_1271 signer must match maker")
    maker, taker = int(str(signed.makerAmount)), int(str(signed.takerAmount))
    if not 0 < taker < maker or int(str(signed.tokenId)) <= 0:
        raise ValueError("invalid signed SELL token/amounts")
    contracts = get_contract_config(int(chain_id))
    exchange = contracts.neg_risk_exchange_v2 if neg_risk else contracts.exchange_v2
    codec = ExchangeOrderBuilderV2(exchange, int(chain_id), signer=None)
    predicted = codec.build_order_hash(codec.build_order_typed_data(signed)).lower()
    if re.fullmatch(r"0x[0-9a-f]{64}", predicted) is None:
        raise ValueError("invalid predicted SELL order hash")
    return dict(predicted_order_id=predicted, token_id=str(signed.tokenId),
                maker_amount_micros=str(maker), taker_amount_micros=str(taker),
                chain_id=int(chain_id), neg_risk=int(neg_risk),
                account_fingerprint=account_fingerprint(chain_id,funder,signature_type))


class PlumExecutionLedger(ExecutionLedger):
    def __init__(self, *args, account_identity=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.account_identity = account_identity
        self._sell_identity = ContextVar(f"plum_sell_identity_{id(self)}", default=None)
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS plum_sell_request_identity (
                submission_id TEXT PRIMARY KEY REFERENCES order_submissions(submission_id),
                predicted_order_id TEXT NOT NULL UNIQUE, token_id TEXT NOT NULL,
                maker_amount_micros TEXT NOT NULL, taker_amount_micros TEXT NOT NULL,
                chain_id INTEGER NOT NULL, neg_risk INTEGER NOT NULL,
                created_at TEXT NOT NULL, account_fingerprint TEXT NOT NULL, response_order_id TEXT,
                response_verified INTEGER NOT NULL DEFAULT 0,
                mismatch INTEGER NOT NULL DEFAULT 0, last_lookup_at TEXT)""")

    def record_intent(self, **kwargs):
        sid = super().record_intent(**kwargs)
        context = self._sell_identity.get()
        if context is not None:
            identity = context['identity']
            if kwargs['side'] != 'SELL' or kwargs['token_id'] != identity['token_id'] or kwargs['simulation']:
                raise SubmissionEvidenceError("pre-POST SELL identity binding mismatch")
            # Returning to submit_and_record (and therefore POST) is impossible
            # before this durable identity write succeeds. A crash here leaves
            # an unresolved no-hash intent, not permission to retry.
            with self._connect() as con:
                con.execute("""INSERT INTO plum_sell_request_identity
                    (submission_id,predicted_order_id,token_id,maker_amount_micros,
                     taker_amount_micros,chain_id,neg_risk,created_at,account_fingerprint)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (sid,identity['predicted_order_id'],identity['token_id'],
                        identity['maker_amount_micros'],identity['taker_amount_micros'],
                        identity['chain_id'],identity['neg_risk'],datetime.now(timezone.utc).isoformat(),
                        identity['account_fingerprint']))
            context['submission_id'] = sid
        return sid

    def submit_sell_with_identity(self, identity, **kwargs):
        if self._sell_identity.get() is not None:
            raise SubmissionEvidenceError("nested SELL identity submission")
        if not self.account_identity or identity.get('account_fingerprint') != self.account_identity:
            raise SubmissionEvidenceError('SELL identity belongs to another or unknown account')
        if (kwargs['side'] != 'SELL' or kwargs['token_id'] != identity['token_id']
                or Decimal(str(kwargs['requested_size'])) != Decimal(identity['maker_amount_micros']) / 1000000
                or abs(Decimal(str(kwargs['requested_price'])) - Decimal(identity['taker_amount_micros']) / Decimal(identity['maker_amount_micros'])) > Decimal('0.000001')):
            raise SubmissionEvidenceError("SELL submission differs from signed identity")
        with self._connect() as con:
            if con.execute('SELECT 1 FROM plum_sell_request_identity WHERE predicted_order_id=?', (identity['predicted_order_id'],)).fetchone():
                raise SubmissionEvidenceError("refusing to resubmit an existing signed SELL")
        context = {'identity':identity}
        token = self._sell_identity.set(context)
        original_submit = kwargs['submit']
        def submit_and_verify():
            response = original_submit()
            if isinstance(response, Mapping):
                returned = response.get('orderID') or response.get('order_id')
                if returned:
                    returned = str(returned).lower()
                    valid_id = re.fullmatch(r'0x[0-9a-f]{64}', returned) is not None
                    matches = valid_id and returned == identity['predicted_order_id']
                    with self._connect() as con:
                        con.execute('UPDATE plum_sell_request_identity SET response_order_id=?,response_verified=?,mismatch=? WHERE submission_id=?',
                                    (returned if valid_id else None, int(matches), int(not matches), context['submission_id']))
                    if not matches:
                        # Keep both public IDs but never bind an unrelated ID or
                        # cancel it. The common ledger retains UNKNOWN outcome.
                        raise SellPostOutcomeUnknown("SELL response hash differs from signed order")
            return response
        def submit_once():
            try:
                return submit_and_verify()
            except Exception as error:
                if hasattr(error, 'status_code'):
                    raise
                raise SellPostOutcomeUnknown('SELL POST/response outcome cannot be proven') from error
        kwargs['submit'] = submit_once
        try:
            return super().submit_and_record(**kwargs)
        finally:
            self._sell_identity.reset(token)

    def recover_sell_order_ids(self, fetch_order, *, limit=1):
        """Bounded exact lookup; missing/failed lookup never means zero fill."""
        if not 1 <= limit <= 5:
            raise ValueError('SELL identity recovery limit must be 1..5')
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute("""SELECT i.* FROM plum_sell_request_identity i
                JOIN order_submissions s ON s.submission_id=i.submission_id
                WHERE i.mismatch=0 AND s.side='SELL' AND s.simulation=0
                AND s.order_id IS NULL AND s.outcome_resolution IS NULL
                AND s.response_status IN ('INTENT','SUBMIT_OUTCOME_UNKNOWN','EVIDENCE_WRITE_FAILED')
                ORDER BY COALESCE(i.last_lookup_at,''),s.submitted_at LIMIT ?""", (limit,))]
            legacy = con.execute("""SELECT COUNT(*) FROM order_submissions s
                LEFT JOIN plum_sell_request_identity i ON i.submission_id=s.submission_id
                WHERE s.side='SELL' AND s.simulation=0 AND s.order_id IS NULL
                  AND s.outcome_resolution IS NULL AND i.submission_id IS NULL
                  AND s.response_status IN ('INTENT','SUBMIT_OUTCOME_UNKNOWN','EVIDENCE_WRITE_FAILED')""").fetchone()[0]
        stats = dict(checked=0, linked=0, unavailable=0, rejected=0, legacy_unidentifiable=legacy)
        for row in rows:
            stats['checked'] += 1
            with self._connect() as con:
                con.execute('UPDATE plum_sell_request_identity SET last_lookup_at=? WHERE submission_id=?',
                            (datetime.now(timezone.utc).isoformat(),row['submission_id']))
            if (not self.account_identity or row['account_fingerprint'] != self.account_identity
                    or re.fullmatch(r'0x[0-9a-f]{64}',row['predicted_order_id']) is None):
                stats['rejected'] += 1
                continue
            try:
                detail = fetch_order(row['predicted_order_id'])
            except Exception:
                stats['unavailable'] += 1
                continue
            try:
                self._validate_exact_sell_lookup(row, detail)
            except (ValueError, TypeError, ArithmeticError):
                stats['rejected'] += 1
                continue
            self.resolve_uncertain_submission(row['submission_id'], resolution='ORDER_ID_LINKED',
                order_id=row['predicted_order_id'], reason='exact pre-POST signed SELL hash + authenticated order identity/size/price verified; not fill proof')
            stats['linked'] += 1
        return stats

    @staticmethod
    def _validate_exact_sell_lookup(row, detail):
        if not isinstance(detail, Mapping) or str(detail.get('id','')).lower() != row['predicted_order_id']:
            raise ValueError('exact SELL ID missing/mismatched')
        token = detail.get('asset_id', detail.get('token_id'))
        if str(token) != row['token_id'] or str(detail.get('side','')).upper() != 'SELL':
            raise ValueError('SELL token/side mismatch')
        if detail.get('asset_id') is not None and detail.get('token_id') is not None and str(detail['asset_id']) != str(detail['token_id']):
            raise ValueError('conflicting token identity')
        expected = Decimal(row['maker_amount_micros']) / 1000000
        raw = Decimal(str(detail.get('original_size')))
        price = Decimal(str(detail.get('price')))
        if not raw.is_finite() or raw <= 0 or not price.is_finite() or not 0 < price < 1:
            raise ValueError('invalid SELL amounts')
        candidates = [n for n in (raw, raw / 1000000) if abs(n-expected) <= Decimal('0.000001')]
        if len(candidates) != 1 or abs(price - Decimal(row['taker_amount_micros']) / Decimal(row['maker_amount_micros'])) > Decimal('0.000001'):
            raise ValueError('SELL order amounts differ from signed envelope')
        status = str(detail.get('status','')).upper().removeprefix('ORDER_STATUS_')
        if status not in {'LIVE','OPEN','MATCHED','FILLED','CANCELED','CANCELLED','CANCELED_MARKET_RESOLVED','INVALID'}:
            raise ValueError('SELL order state unavailable')
        if detail.get('order_type') is not None and str(detail['order_type']).upper() != 'FOK':
            raise ValueError('SELL order type contradicts recorded FOK submission')
