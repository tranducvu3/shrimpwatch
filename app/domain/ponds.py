from app.core.config import settings


def pond_id(index: int) -> str:
    return f"pond-{index:02d}"


def pond_ids(count: int | None = None) -> list[str]:
    return [pond_id(i) for i in range(1, (count or settings.NUM_PONDS) + 1)]
