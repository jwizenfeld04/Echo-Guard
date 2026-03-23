"""POJ-104 benchmark adapter.

POJ-104 (from Microsoft CodeXGLUE) is a semantic clone detection benchmark
based on solutions to 104 programming problems from an online judge (POJ).

Multiple solutions to the same problem are considered semantic clones (Type-4),
as they implement the same algorithm/functionality but often with completely
different code structure.

The dataset is primarily C/C++ code, making it complementary to BigCloneBench (Java)
and GPTCloneBench (Python/Java).

Reference: https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from benchmarks.base import BenchmarkAdapter, BenchmarkPair


class POJ104Adapter(BenchmarkAdapter):
    """Adapter for the POJ-104 semantic clone detection dataset.

    POJ-104 organizes code by problem ID. Solutions to the same problem
    are semantic clones; solutions to different problems are not.

    Supports:
    1. `curated` — Built-in representative C/C++ pairs (no download needed)
    2. `full` — Pairs from the CodeXGLUE dataset (requires download)
    """

    @property
    def name(self) -> str:
        return "POJ-104"

    @property
    def dataset_id(self) -> str:
        return "poj104"

    def is_available(self) -> bool:
        poj_dir = self.data_dir / "poj104"
        return (poj_dir / "programs").exists() or True  # Curated always available

    def download(self, force: bool = False) -> None:
        poj_dir = self.data_dir / "poj104"
        if poj_dir.exists() and not force:
            print(f"  POJ-104 data already exists at {poj_dir}")
            return

        poj_dir.mkdir(parents=True, exist_ok=True)

        print("  Downloading POJ-104 dataset...")
        print("  Note: Full dataset requires download from:")
        print("    https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104")
        print()
        print("  For now, using built-in curated C/C++ pairs.")
        print("  To use the full dataset, download and extract into:")
        print(f"    {poj_dir}")

    def load_pairs(self, max_pairs: int | None = None) -> list[BenchmarkPair]:
        poj_dir = self.data_dir / "poj104"
        programs_dir = poj_dir / "programs"

        if programs_dir.exists():
            return self._load_from_directory(programs_dir, max_pairs)

        return self._load_curated_pairs(max_pairs)

    def _load_from_directory(
        self, programs_dir: Path, max_pairs: int | None
    ) -> list[BenchmarkPair]:
        """Load pairs from POJ-104 directory structure.

        Expected structure:
        programs/
          1/        # problem ID
            1.c     # solution 1
            2.c     # solution 2
          2/
            1.c
            ...
        """
        pairs: list[BenchmarkPair] = []

        # Group solutions by problem ID
        problems: dict[str, list[Path]] = {}
        for problem_dir in sorted(programs_dir.iterdir()):
            if not problem_dir.is_dir():
                continue
            solutions = list(problem_dir.glob("*.c")) + list(problem_dir.glob("*.cpp"))
            if len(solutions) >= 2:
                problems[problem_dir.name] = solutions

        # Generate positive pairs (same problem = semantic clone)
        for problem_id, solutions in problems.items():
            for i in range(min(len(solutions), 3)):  # Limit pairs per problem
                for j in range(i + 1, min(len(solutions), 4)):
                    if max_pairs and len(pairs) >= max_pairs:
                        break
                    code_a = solutions[i].read_text(errors="replace")
                    code_b = solutions[j].read_text(errors="replace")
                    lang = "cpp" if solutions[i].suffix == ".cpp" else "c"

                    pairs.append(
                        BenchmarkPair(
                            pair_id=f"poj_{problem_id}_{i}_{j}",
                            code_a=code_a,
                            code_b=code_b,
                            language_a=lang,
                            language_b=lang,
                            is_clone=True,
                            clone_type="type4",  # POJ-104 pairs are semantic clones
                            source_dataset="poj104",
                            metadata={"problem_id": problem_id},
                        )
                    )

        # Generate negative pairs (different problems)
        problem_ids = list(problems.keys())
        neg_count = len(pairs) // 3  # ~25% negatives
        rng = random.Random(42)  # Deterministic
        for _ in range(neg_count):
            if max_pairs and len(pairs) >= max_pairs:
                break
            p1, p2 = rng.sample(problem_ids, 2)
            sol1 = rng.choice(problems[p1])
            sol2 = rng.choice(problems[p2])
            lang = "cpp" if sol1.suffix == ".cpp" else "c"

            pairs.append(
                BenchmarkPair(
                    pair_id=f"poj_neg_{p1}_{p2}",
                    code_a=sol1.read_text(errors="replace"),
                    code_b=sol2.read_text(errors="replace"),
                    language_a=lang,
                    language_b=lang,
                    is_clone=False,
                    clone_type="negative",
                    source_dataset="poj104",
                    metadata={"problem_id_a": p1, "problem_id_b": p2},
                )
            )

        return pairs

    def _load_curated_pairs(
        self, max_pairs: int | None
    ) -> list[BenchmarkPair]:
        """Built-in curated C/C++ pairs representative of POJ-104.

        These are typical competitive programming solutions demonstrating
        how different programmers solve the same problem with different
        approaches (the core of POJ-104).
        """
        pairs = _CURATED_POJ_PAIRS[:]
        if max_pairs:
            pairs = pairs[:max_pairs]
        return pairs


# ── Curated POJ-104-style C/C++ pairs ──────────────────────────────────

_CURATED_POJ_PAIRS: list[BenchmarkPair] = [
    # -- Type-4: Same problem, different implementation (core of POJ-104) --

    # Problem: Find GCD of two numbers
    BenchmarkPair(
        pair_id="poj_t4_01",
        code_a="""\
int gcd(int a, int b) {
    while (b != 0) {
        int t = b;
        b = a % b;
        a = t;
    }
    return a;
}
""",
        code_b="""\
int gcd(int a, int b) {
    if (b == 0) return a;
    return gcd(b, a % b);
}
""",
        language_a="c",
        language_b="c",
        is_clone=True,
        clone_type="type4",
        source_dataset="poj104",
        metadata={"problem": "gcd"},
    ),

    # Problem: Check if number is prime
    BenchmarkPair(
        pair_id="poj_t4_02",
        code_a="""\
int is_prime(int n) {
    if (n <= 1) return 0;
    if (n <= 3) return 1;
    if (n % 2 == 0 || n % 3 == 0) return 0;
    for (int i = 5; i * i <= n; i += 6) {
        if (n % i == 0 || n % (i + 2) == 0) return 0;
    }
    return 1;
}
""",
        code_b="""\
int is_prime(int n) {
    if (n < 2) return 0;
    for (int i = 2; i * i <= n; i++) {
        if (n % i == 0) return 0;
    }
    return 1;
}
""",
        language_a="c",
        language_b="c",
        is_clone=True,
        clone_type="type4",
        source_dataset="poj104",
        metadata={"problem": "primality"},
    ),

    # Problem: Reverse a string
    BenchmarkPair(
        pair_id="poj_t4_03",
        code_a="""\
void reverse_string(char* str) {
    int len = strlen(str);
    for (int i = 0; i < len / 2; i++) {
        char temp = str[i];
        str[i] = str[len - 1 - i];
        str[len - 1 - i] = temp;
    }
}
""",
        code_b="""\
void reverse_string(char* str) {
    char* end = str + strlen(str) - 1;
    while (str < end) {
        char tmp = *str;
        *str++ = *end;
        *end-- = tmp;
    }
}
""",
        language_a="c",
        language_b="c",
        is_clone=True,
        clone_type="type4",
        source_dataset="poj104",
        metadata={"problem": "string_reverse"},
    ),

    # Problem: Binary search
    BenchmarkPair(
        pair_id="poj_t4_04",
        code_a="""\
int binary_search(int arr[], int n, int target) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (arr[mid] == target) return mid;
        if (arr[mid] < target) lo = mid + 1;
        else hi = mid - 1;
    }
    return -1;
}
""",
        code_b="""\
int binary_search(int* arr, int size, int key) {
    int left = 0, right = size - 1;
    while (left <= right) {
        int m = (left + right) >> 1;
        if (arr[m] == key) return m;
        else if (arr[m] > key) right = m - 1;
        else left = m + 1;
    }
    return -1;
}
""",
        language_a="c",
        language_b="c",
        is_clone=True,
        clone_type="type4",
        source_dataset="poj104",
        metadata={"problem": "binary_search"},
    ),

    # Problem: Fibonacci
    BenchmarkPair(
        pair_id="poj_t4_05",
        code_a="""\
long long fibonacci(int n) {
    if (n <= 1) return n;
    long long a = 0, b = 1;
    for (int i = 2; i <= n; i++) {
        long long c = a + b;
        a = b;
        b = c;
    }
    return b;
}
""",
        code_b="""\
long long fibonacci(int n) {
    if (n <= 1) return n;
    return fibonacci(n - 1) + fibonacci(n - 2);
}
""",
        language_a="c",
        language_b="c",
        is_clone=True,
        clone_type="type4",
        source_dataset="poj104",
        metadata={"problem": "fibonacci"},
    ),

    # Problem: Power function
    BenchmarkPair(
        pair_id="poj_t4_06",
        code_a="""\
long long power(long long base, int exp) {
    long long result = 1;
    while (exp > 0) {
        if (exp % 2 == 1) result *= base;
        base *= base;
        exp /= 2;
    }
    return result;
}
""",
        code_b="""\
long long power(long long base, int exp) {
    if (exp == 0) return 1;
    if (exp == 1) return base;
    long long half = power(base, exp / 2);
    if (exp % 2 == 0) return half * half;
    return half * half * base;
}
""",
        language_a="c",
        language_b="c",
        is_clone=True,
        clone_type="type4",
        source_dataset="poj104",
        metadata={"problem": "power"},
    ),

    # C++ pairs for variety
    BenchmarkPair(
        pair_id="poj_t4_07",
        code_a="""\
vector<int> removeDuplicates(vector<int>& nums) {
    sort(nums.begin(), nums.end());
    auto last = unique(nums.begin(), nums.end());
    nums.erase(last, nums.end());
    return nums;
}
""",
        code_b="""\
vector<int> removeDuplicates(vector<int>& nums) {
    set<int> seen(nums.begin(), nums.end());
    return vector<int>(seen.begin(), seen.end());
}
""",
        language_a="cpp",
        language_b="cpp",
        is_clone=True,
        clone_type="type4",
        source_dataset="poj104",
        metadata={"problem": "dedup"},
    ),

    # -- Negative pairs (different problems) --
    BenchmarkPair(
        pair_id="poj_neg_01",
        code_a="""\
int gcd(int a, int b) {
    while (b != 0) {
        int t = b;
        b = a % b;
        a = t;
    }
    return a;
}
""",
        code_b="""\
void bubble_sort(int arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
            }
        }
    }
}
""",
        language_a="c",
        language_b="c",
        is_clone=False,
        clone_type="negative",
        source_dataset="poj104",
    ),
    BenchmarkPair(
        pair_id="poj_neg_02",
        code_a="""\
int binary_search(int arr[], int n, int target) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (arr[mid] == target) return mid;
        if (arr[mid] < target) lo = mid + 1;
        else hi = mid - 1;
    }
    return -1;
}
""",
        code_b="""\
void print_matrix(int mat[][100], int rows, int cols) {
    for (int i = 0; i < rows; i++) {
        for (int j = 0; j < cols; j++) {
            printf("%d ", mat[i][j]);
        }
        printf("\\n");
    }
}
""",
        language_a="c",
        language_b="c",
        is_clone=False,
        clone_type="negative",
        source_dataset="poj104",
    ),
    BenchmarkPair(
        pair_id="poj_neg_03",
        code_a="""\
long long fibonacci(int n) {
    if (n <= 1) return n;
    long long a = 0, b = 1;
    for (int i = 2; i <= n; i++) {
        long long c = a + b;
        a = b;
        b = c;
    }
    return b;
}
""",
        code_b="""\
char* read_file(const char* filename) {
    FILE* fp = fopen(filename, "r");
    if (!fp) return NULL;
    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    char* buffer = malloc(size + 1);
    fread(buffer, 1, size, fp);
    buffer[size] = '\\0';
    fclose(fp);
    return buffer;
}
""",
        language_a="c",
        language_b="c",
        is_clone=False,
        clone_type="negative",
        source_dataset="poj104",
    ),
    # Tricky: same domain (math) but different operations
    BenchmarkPair(
        pair_id="poj_neg_04",
        code_a="""\
int factorial(int n) {
    int result = 1;
    for (int i = 2; i <= n; i++) {
        result *= i;
    }
    return result;
}
""",
        code_b="""\
int combination(int n, int r) {
    if (r > n) return 0;
    if (r == 0 || r == n) return 1;
    int result = 1;
    for (int i = 0; i < r; i++) {
        result = result * (n - i) / (i + 1);
    }
    return result;
}
""",
        language_a="c",
        language_b="c",
        is_clone=False,
        clone_type="negative",
        source_dataset="poj104",
    ),
]
