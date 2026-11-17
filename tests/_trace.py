def covers(*req_ids: str):
    """Marks a test as evidence for requirements in SPEC.md. Read by scripts/trace_matrix.py."""

    def deco(fn):
        fn.covers = tuple(req_ids)
        return fn

    return deco
