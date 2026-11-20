"""Named errors for broken invariants. Each one subclasses ValueError, so callers that caught the former
generic ValueError keep working."""


class EmptyFeatureTable(ValueError):
    """An empty feature DataFrame must never replace the published table."""


class SourceGroupCollision(ValueError):
    """Two tier-2 source groups normalize to the same column prefix."""


class EmptySourceGroupPrefix(ValueError):
    """A tier-2 source group normalizes to an empty column prefix."""


class InvalidIdentifier(ValueError):
    """An SQL identifier is empty or contains a quote character or a backslash."""
