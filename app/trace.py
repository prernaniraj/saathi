def implements(*req_ids: str):
    """Marks code that implements requirements in SPEC.md. Read by scripts/trace_matrix.py."""

    def deco(fn):
        fn.reqs = tuple(req_ids)
        return fn

    return deco
