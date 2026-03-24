"""AST tree edit distance for precise structural similarity scoring.

Computes normalized edit distance between two functions' AST token
sequences using the Zhang-Shasha algorithm. This provides a continuous
structural similarity signal between the binary AST hash (identical or not)
and the noisy embedding cosine score.

Performance: O(n*m*min(depth_a, depth_b)*min(depth_b, depth_a)) where
n,m are node counts. Typical functions have 20-100 nodes → <1ms per pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimpleNode:
    """A simple tree node for edit distance computation."""
    label: str
    children: list[SimpleNode] = field(default_factory=list)


def parse_token_tree(tokens: str) -> SimpleNode:
    """Convert a parenthesized token string into a tree structure.

    Input format (from _compute_structural_hash):
        "(function_definition (parameters v0 v1) (block (return_statement (call v2 v0))))"

    Returns a SimpleNode tree. If parsing fails, returns a single node
    with the entire token string as label (graceful degradation).
    """
    if not tokens or not tokens.strip():
        return SimpleNode(label="empty")

    pos = 0
    chars = tokens.strip()

    def _parse() -> SimpleNode:
        nonlocal pos

        # Skip whitespace
        while pos < len(chars) and chars[pos] == " ":
            pos += 1

        if pos >= len(chars):
            return SimpleNode(label="empty")

        if chars[pos] == "(":
            # Start of a node with children: "(label child1 child2 ...)"
            pos += 1  # skip "("

            # Read the label (until space or "(" or ")")
            label_start = pos
            while pos < len(chars) and chars[pos] not in (" ", "(", ")"):
                pos += 1
            label = chars[label_start:pos]

            children = []
            while pos < len(chars) and chars[pos] != ")":
                # Skip whitespace
                while pos < len(chars) and chars[pos] == " ":
                    pos += 1
                if pos < len(chars) and chars[pos] != ")":
                    children.append(_parse())

            if pos < len(chars) and chars[pos] == ")":
                pos += 1  # skip ")"

            return SimpleNode(label=label, children=children)
        else:
            # Leaf token: read until space or ")"
            token_start = pos
            while pos < len(chars) and chars[pos] not in (" ", ")"):
                pos += 1
            token = chars[token_start:pos]
            return SimpleNode(label=token)

    try:
        return _parse()
    except (IndexError, RecursionError):
        # Graceful degradation for malformed token strings
        return SimpleNode(label="unparseable")


def _tree_size(node: SimpleNode) -> int:
    """Count total nodes in a tree."""
    return 1 + sum(_tree_size(c) for c in node.children)


def _leftmost_leaf_descendants(node: SimpleNode) -> list[int]:
    """Compute leftmost leaf descendant for each node (post-order indexed)."""
    result: list[int] = []
    _compute_lld(node, result)
    return result


def _compute_lld(node: SimpleNode, result: list[int]) -> int:
    """Compute leftmost leaf descendants recursively, return post-order index."""
    if not node.children:
        idx = len(result)
        result.append(idx)  # leaf's leftmost descendant is itself
        return idx

    child_indices = []
    for child in node.children:
        child_indices.append(_compute_lld(child, result))

    idx = len(result)
    result.append(result[child_indices[0]])  # leftmost leaf of first child
    return idx


def _post_order_nodes(node: SimpleNode) -> list[SimpleNode]:
    """Return nodes in post-order traversal."""
    result: list[SimpleNode] = []

    def _traverse(n: SimpleNode) -> None:
        for child in n.children:
            _traverse(child)
        result.append(n)

    _traverse(node)
    return result


def tree_edit_distance(a: SimpleNode, b: SimpleNode) -> int:
    """Zhang-Shasha tree edit distance between two trees.

    Cost model: insert=1, delete=1, rename=1 (if labels differ), rename=0 (if same).
    """
    nodes_a = _post_order_nodes(a)
    nodes_b = _post_order_nodes(b)
    n = len(nodes_a)
    m = len(nodes_b)

    if n == 0 or m == 0:
        return max(n, m)

    lld_a = _leftmost_leaf_descendants(a)
    lld_b = _leftmost_leaf_descendants(b)

    # Key roots: nodes where lld[i] != lld[parent[i]]
    # For simplicity, compute key roots as nodes that are roots or
    # whose leftmost leaf descendant differs from their parent's
    kr_a = _key_roots(lld_a, n)
    kr_b = _key_roots(lld_b, m)

    # Forest distance and tree distance tables
    td = [[0] * (m + 1) for _ in range(n + 1)]
    fd = [[0] * (m + 1) for _ in range(n + 1)]

    for i in kr_a:
        for j in kr_b:
            li = lld_a[i]
            lj = lld_b[j]

            fd[li][lj] = 0
            for x in range(li, i + 1):
                fd[x + 1][lj] = fd[x][lj] + 1  # delete
            for y in range(lj, j + 1):
                fd[li][y + 1] = fd[li][y] + 1  # insert

            for x in range(li, i + 1):
                for y in range(lj, j + 1):
                    lx = lld_a[x]
                    ly = lld_b[y]

                    if lx == li and ly == lj:
                        # Both are in the same subforest
                        cost = 0 if nodes_a[x].label == nodes_b[y].label else 1
                        fd[x + 1][y + 1] = min(
                            fd[x][y + 1] + 1,      # delete
                            fd[x + 1][y] + 1,       # insert
                            fd[x][y] + cost,         # rename
                        )
                        td[x][y] = fd[x + 1][y + 1]
                    else:
                        fd[x + 1][y + 1] = min(
                            fd[x][y + 1] + 1,       # delete
                            fd[x + 1][y] + 1,        # insert
                            fd[lx][ly] + td[x][y],  # use previously computed tree distance
                        )

    return td[n - 1][m - 1] if n > 0 and m > 0 else max(n, m)


def _key_roots(lld: list[int], size: int) -> list[int]:
    """Compute key roots for Zhang-Shasha algorithm."""
    visited: set[int] = set()
    kr: list[int] = []
    for i in range(size - 1, -1, -1):
        if lld[i] not in visited:
            kr.append(i)
            visited.add(lld[i])
    kr.sort()
    return kr


def normalized_ast_similarity(tokens_a: str, tokens_b: str) -> float:
    """Compute normalized AST similarity between two functions.

    Returns 1.0 for identical trees, 0.0 for completely different.

    Uses a tiered approach for performance:
    - Identical tokens → 1.0 (instant)
    - Small trees (≤60 nodes) → Zhang-Shasha edit distance (precise, <1ms)
    - Larger trees → token sequence ratio (fast approximation, <0.1ms)
    """
    if not tokens_a or not tokens_b:
        return 0.0

    # Fast path: identical tokens → identical structure
    if tokens_a == tokens_b:
        return 1.0

    # Quick size check before parsing
    len_a = tokens_a.count(" ") + 1
    len_b = tokens_b.count(" ") + 1

    # Very different sizes → quick ratio estimate
    size_ratio = min(len_a, len_b) / max(len_a, len_b) if max(len_a, len_b) > 0 else 1.0
    if size_ratio < 0.5:
        return size_ratio * 0.7  # Very different sizes = low similarity

    # For larger trees, use fast token sequence comparison
    # Zhang-Shasha is O(n²m²) — only worth it for small trees
    MAX_NODES_FOR_TREE_EDIT = 60
    if len_a > MAX_NODES_FOR_TREE_EDIT or len_b > MAX_NODES_FOR_TREE_EDIT:
        return _token_sequence_similarity(tokens_a, tokens_b)

    tree_a = parse_token_tree(tokens_a)
    tree_b = parse_token_tree(tokens_b)

    size_a = _tree_size(tree_a)
    size_b = _tree_size(tree_b)

    max_size = max(size_a, size_b)
    if max_size == 0:
        return 1.0

    dist = tree_edit_distance(tree_a, tree_b)
    return max(0.0, 1.0 - (dist / max_size))


def _token_sequence_similarity(tokens_a: str, tokens_b: str) -> float:
    """Fast similarity using token set overlap (Jaccard-like).

    Much faster than SequenceMatcher — O(n) instead of O(n²).
    Uses token multiset intersection to approximate structural similarity.
    """
    toks_a = tokens_a.split()
    toks_b = tokens_b.split()

    # Use multiset (Counter) intersection for O(n) comparison
    from collections import Counter
    count_a = Counter(toks_a)
    count_b = Counter(toks_b)

    # Intersection: min count for each shared token
    intersection = sum((count_a & count_b).values())
    union = sum((count_a | count_b).values())

    return intersection / union if union > 0 else 0.0
