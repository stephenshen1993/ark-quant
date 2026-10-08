"""人工确认的已发起调拨事实；不提交转账、不把建议当作已执行。"""
from datasource.account_store import validate_nonnegative_finite

CARRIERS = {'cash', 'stock', 'cb', 'pingan'}


def normalize_transfers(source: str, rows: list[dict]) -> list[dict]:
    from app.account_current_state import CurrentAccountError
    if source not in CARRIERS and rows:
        raise CurrentAccountError('投顾赎回请使用确认赎回在途字段，不要重复登记')
    result = []
    targets = set()
    for row in rows:
        target = row.get('target')
        if not isinstance(target, str) or target not in CARRIERS or target == source or ('cash' not in (source, target)):
            raise CurrentAccountError('跨账户调拨必须经资金账户中转')
        # 每个目标/扣账状态汇总一行；两种状态可同时存在。
        if not isinstance(row.get('debited'), bool):
            raise CurrentAccountError('请明确调拨是否已从来源余额扣除')
        key = (target, row['debited'])
        if key in targets:
            raise CurrentAccountError('同一目标及扣账状态请合并金额，避免重复记录')
        amount = validate_nonnegative_finite(row.get('amount'), '调拨金额')
        if round(amount, 2) <= 0:
            raise CurrentAccountError('已发起调拨金额必须大于零')
        targets.add(key)
        result.append(dict(target=target, amount=round(amount, 2), debited=row['debited']))
    return sorted(result, key=lambda item: (item['target'], item['debited']))
