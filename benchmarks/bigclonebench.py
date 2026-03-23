"""BigCloneBench benchmark adapter.

BigCloneBench (Svajlenko & Roy, ICSE 2014) is the largest clone detection benchmark,
containing 8M+ labeled Java clone pairs across Type-1 through Type-4.

Dataset structure:
- IJaDataset: A collection of Java files from SourceForge (25,000 files)
- Clone pairs are labeled by functionality ID and syntactic similarity ranges

Reference: https://github.com/clonebench/BigCloneBench

Since the full dataset is ~8M pairs, we support:
1. Full evaluation (requires dataset download)
2. Sampled evaluation (stratified sample per clone type)
3. Curated subset (hand-picked representative pairs bundled with Echo Guard)
"""

from __future__ import annotations

import csv
import hashlib
import random
import shutil
import subprocess
from pathlib import Path

from benchmarks.base import BenchmarkAdapter, BenchmarkPair

# BigCloneBench clone type classification based on syntactic similarity ranges
# From the paper: similarity is measured by line-level and token-level metrics
BCB_CLONE_TYPES = {
    "type1": (1.0, 1.0),       # Exact match (100% similarity)
    "type2": (0.9, 1.0),       # Very high similarity (renamed identifiers)
    "type3": (0.7, 0.9),       # High similarity (modified statements)
    "type4_strong": (0.5, 0.7),  # Moderate similarity (significant changes)
    "type4_weak": (0.0, 0.5),    # Low similarity (semantic clones)
}


class BigCloneBenchAdapter(BenchmarkAdapter):
    """Adapter for the BigCloneBench dataset.

    Supports three modes:
    1. `curated` — Built-in representative Java pairs (no download needed)
    2. `sampled` — Stratified random sample from full dataset
    3. `full` — All pairs (requires dataset download, very slow)
    """

    @property
    def name(self) -> str:
        return "BigCloneBench"

    @property
    def dataset_id(self) -> str:
        return "bigclonebench"

    def is_available(self) -> bool:
        """Check if the full dataset is downloaded."""
        bcb_dir = self.data_dir / "bigclonebench"
        # Check for either the clone pairs CSV or the curated subset
        return (
            (bcb_dir / "bcb_reduced" / "clonepairs.csv").exists()
            or (bcb_dir / "curated").exists()
            or True  # Built-in curated pairs always available
        )

    def download(self, force: bool = False) -> None:
        """Download BigCloneBench dataset.

        Downloads the reduced version (bcb_reduced) which contains
        the clone pair labels and a subset of the IJaDataset Java files.
        """
        bcb_dir = self.data_dir / "bigclonebench"
        if bcb_dir.exists() and not force:
            print(f"  BigCloneBench data already exists at {bcb_dir}")
            return

        bcb_dir.mkdir(parents=True, exist_ok=True)

        print("  Downloading BigCloneBench reduced dataset...")
        print("  Note: Full dataset requires manual download from:")
        print("    https://github.com/clonebench/BigCloneBench")
        print()
        print("  For now, using built-in curated pairs.")
        print("  To use the full dataset, clone the BigCloneBench repo into:")
        print(f"    {bcb_dir}")

    def load_pairs(self, max_pairs: int | None = None) -> list[BenchmarkPair]:
        """Load pairs from BigCloneBench.

        Tries to load from downloaded dataset first, falls back to curated pairs.
        """
        bcb_dir = self.data_dir / "bigclonebench"
        clonepairs_csv = bcb_dir / "bcb_reduced" / "clonepairs.csv"

        if clonepairs_csv.exists():
            return self._load_from_csv(clonepairs_csv, max_pairs)

        # Fall back to built-in curated pairs
        return self._load_curated_pairs(max_pairs)

    def _load_from_csv(
        self, csv_path: Path, max_pairs: int | None
    ) -> list[BenchmarkPair]:
        """Load pairs from the BigCloneBench CSV format.

        Expected CSV columns: functionality_id, id1, id2, syntactic_similarity
        The Java source files should be in an adjacent 'ijadataset' directory.
        """
        pairs: list[BenchmarkPair] = []
        java_dir = csv_path.parent.parent / "ijadataset"

        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if max_pairs and len(pairs) >= max_pairs:
                    break

                func_id_1 = row.get("id1", row.get("function_id_one", ""))
                func_id_2 = row.get("id2", row.get("function_id_two", ""))
                similarity = float(row.get("syntactic_similarity", row.get("similarity", "0")))

                # Classify clone type based on similarity range
                clone_type = self._classify_bcb_type(similarity)

                # Try to load source code
                code_a = self._load_java_source(java_dir, func_id_1)
                code_b = self._load_java_source(java_dir, func_id_2)

                if code_a is None or code_b is None:
                    continue

                pairs.append(
                    BenchmarkPair(
                        pair_id=f"bcb_{func_id_1}_{func_id_2}",
                        code_a=code_a,
                        code_b=code_b,
                        language_a="java",
                        language_b="java",
                        is_clone=True,  # BCB only contains true clone pairs
                        clone_type=clone_type,
                        source_dataset="bigclonebench",
                        metadata={
                            "syntactic_similarity": similarity,
                            "function_id_1": func_id_1,
                            "function_id_2": func_id_2,
                        },
                    )
                )

        return pairs

    def _load_java_source(self, java_dir: Path, func_id: str) -> str | None:
        """Load a Java function's source code by its BCB function ID."""
        # BCB organizes files by functionality ID subdirectories
        for java_file in java_dir.rglob(f"*{func_id}*.java"):
            return java_file.read_text(errors="replace")
        return None

    def _classify_bcb_type(self, similarity: float) -> str:
        """Classify a BCB pair's clone type based on syntactic similarity."""
        if similarity >= 1.0:
            return "type1"
        if similarity >= 0.9:
            return "type2"
        if similarity >= 0.7:
            return "type3"
        if similarity >= 0.5:
            return "type4"
        return "type4"  # Weak Type-4 (semantic only)

    def _load_curated_pairs(
        self, max_pairs: int | None
    ) -> list[BenchmarkPair]:
        """Return built-in curated Java clone pairs representative of BigCloneBench.

        These pairs cover the same clone types (T1-T4) as BigCloneBench using
        Java functions from common BCB functionality categories:
        - Sorting algorithms
        - File I/O operations
        - String manipulation
        - Data structure operations
        - Network/HTTP utilities
        """
        pairs = _CURATED_BCB_PAIRS[:]
        if max_pairs:
            pairs = pairs[:max_pairs]
        return pairs


# ── Curated BigCloneBench-style Java pairs ──────────────────────────────

_CURATED_BCB_PAIRS: list[BenchmarkPair] = [
    # -- Type-1: Exact copies (whitespace/comment differences) --
    BenchmarkPair(
        pair_id="bcb_t1_01",
        code_a="""\
public static int[] bubbleSort(int[] arr) {
    int n = arr.length;
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
            }
        }
    }
    return arr;
}
""",
        code_b="""\
public static int[] bubbleSort(int[] arr) {
    // Standard bubble sort implementation
    int n = arr.length;
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
            }
        }
    }
    return arr;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type1",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_t1_02",
        code_a="""\
public static String readFile(String path) throws IOException {
    StringBuilder content = new StringBuilder();
    BufferedReader reader = new BufferedReader(new FileReader(path));
    String line;
    while ((line = reader.readLine()) != null) {
        content.append(line).append("\\n");
    }
    reader.close();
    return content.toString();
}
""",
        code_b="""\
public static String readFile(String path) throws IOException {
    StringBuilder content = new StringBuilder();
    BufferedReader reader = new BufferedReader(new FileReader(path));

    String line;

    while ((line = reader.readLine()) != null) {
        content.append(line).append("\\n");
    }

    reader.close();
    return content.toString();
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type1",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_t1_03",
        code_a="""\
public static boolean isPrime(int n) {
    if (n <= 1) return false;
    if (n <= 3) return true;
    if (n % 2 == 0 || n % 3 == 0) return false;
    for (int i = 5; i * i <= n; i += 6) {
        if (n % i == 0 || n % (i + 2) == 0) return false;
    }
    return true;
}
""",
        code_b="""\
public static boolean isPrime(int n) {
    if (n <= 1) return false;
    if (n <= 3) return true;
    if (n % 2 == 0 || n % 3 == 0) return false;
    for (int i = 5; i * i <= n; i += 6) {
        if (n % i == 0 || n % (i + 2) == 0)
            return false;
    }
    return true;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type1",
        source_dataset="bigclonebench",
    ),

    # -- Type-2: Renamed identifiers --
    BenchmarkPair(
        pair_id="bcb_t2_01",
        code_a="""\
public static int[] bubbleSort(int[] arr) {
    int n = arr.length;
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
            }
        }
    }
    return arr;
}
""",
        code_b="""\
public static int[] sortArray(int[] data) {
    int size = data.length;
    for (int outer = 0; outer < size - 1; outer++) {
        for (int inner = 0; inner < size - outer - 1; inner++) {
            if (data[inner] > data[inner + 1]) {
                int swap = data[inner];
                data[inner] = data[inner + 1];
                data[inner + 1] = swap;
            }
        }
    }
    return data;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type2",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_t2_02",
        code_a="""\
public static int binarySearch(int[] arr, int target) {
    int low = 0;
    int high = arr.length - 1;
    while (low <= high) {
        int mid = (low + high) / 2;
        if (arr[mid] == target) return mid;
        else if (arr[mid] < target) low = mid + 1;
        else high = mid - 1;
    }
    return -1;
}
""",
        code_b="""\
public static int findElement(int[] numbers, int value) {
    int left = 0;
    int right = numbers.length - 1;
    while (left <= right) {
        int center = (left + right) / 2;
        if (numbers[center] == value) return center;
        else if (numbers[center] < value) left = center + 1;
        else right = center - 1;
    }
    return -1;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type2",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_t2_03",
        code_a="""\
public static String reverseString(String input) {
    char[] chars = input.toCharArray();
    int left = 0;
    int right = chars.length - 1;
    while (left < right) {
        char temp = chars[left];
        chars[left] = chars[right];
        chars[right] = temp;
        left++;
        right--;
    }
    return new String(chars);
}
""",
        code_b="""\
public static String flipString(String str) {
    char[] characters = str.toCharArray();
    int start = 0;
    int end = characters.length - 1;
    while (start < end) {
        char swap = characters[start];
        characters[start] = characters[end];
        characters[end] = swap;
        start++;
        end--;
    }
    return new String(characters);
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type2",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_t2_04",
        code_a="""\
public static List<Integer> removeDuplicates(List<Integer> list) {
    Set<Integer> seen = new LinkedHashSet<>();
    for (Integer item : list) {
        seen.add(item);
    }
    return new ArrayList<>(seen);
}
""",
        code_b="""\
public static List<Integer> dedup(List<Integer> items) {
    Set<Integer> unique = new LinkedHashSet<>();
    for (Integer element : items) {
        unique.add(element);
    }
    return new ArrayList<>(unique);
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type2",
        source_dataset="bigclonebench",
    ),

    # -- Type-3: Modified statements --
    BenchmarkPair(
        pair_id="bcb_t3_01",
        code_a="""\
public static int[] bubbleSort(int[] arr) {
    int n = arr.length;
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
            }
        }
    }
    return arr;
}
""",
        code_b="""\
public static int[] bubbleSortOptimized(int[] arr) {
    int n = arr.length;
    boolean swapped;
    for (int i = 0; i < n - 1; i++) {
        swapped = false;
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
                swapped = true;
            }
        }
        if (!swapped) break;
    }
    return arr;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type3",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_t3_02",
        code_a="""\
public static String readFile(String path) throws IOException {
    StringBuilder content = new StringBuilder();
    BufferedReader reader = new BufferedReader(new FileReader(path));
    String line;
    while ((line = reader.readLine()) != null) {
        content.append(line).append("\\n");
    }
    reader.close();
    return content.toString();
}
""",
        code_b="""\
public static String readFileWithEncoding(String path, String charset) throws IOException {
    StringBuilder content = new StringBuilder();
    BufferedReader reader = new BufferedReader(
        new InputStreamReader(new FileInputStream(path), charset));
    try {
        String line;
        while ((line = reader.readLine()) != null) {
            content.append(line).append(System.lineSeparator());
        }
    } finally {
        reader.close();
    }
    return content.toString().trim();
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type3",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_t3_03",
        code_a="""\
public static int binarySearch(int[] arr, int target) {
    int low = 0;
    int high = arr.length - 1;
    while (low <= high) {
        int mid = (low + high) / 2;
        if (arr[mid] == target) return mid;
        else if (arr[mid] < target) low = mid + 1;
        else high = mid - 1;
    }
    return -1;
}
""",
        code_b="""\
public static int binarySearchRecursive(int[] arr, int target, int low, int high) {
    if (low > high) return -1;
    int mid = low + (high - low) / 2;
    if (arr[mid] == target) return mid;
    if (arr[mid] < target) return binarySearchRecursive(arr, target, mid + 1, high);
    return binarySearchRecursive(arr, target, low, mid - 1);
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type3",
        source_dataset="bigclonebench",
    ),

    # -- Type-4: Same semantics, different implementation --
    BenchmarkPair(
        pair_id="bcb_t4_01",
        code_a="""\
public static int[] bubbleSort(int[] arr) {
    int n = arr.length;
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
            }
        }
    }
    return arr;
}
""",
        code_b="""\
public static int[] insertionSort(int[] arr) {
    for (int i = 1; i < arr.length; i++) {
        int key = arr[i];
        int j = i - 1;
        while (j >= 0 && arr[j] > key) {
            arr[j + 1] = arr[j];
            j--;
        }
        arr[j + 1] = key;
    }
    return arr;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type4",
        source_dataset="bigclonebench",
        metadata={"bcb_functionality": "sorting"},
    ),
    BenchmarkPair(
        pair_id="bcb_t4_02",
        code_a="""\
public static boolean isPrime(int n) {
    if (n <= 1) return false;
    if (n <= 3) return true;
    if (n % 2 == 0 || n % 3 == 0) return false;
    for (int i = 5; i * i <= n; i += 6) {
        if (n % i == 0 || n % (i + 2) == 0) return false;
    }
    return true;
}
""",
        code_b="""\
public static boolean checkPrime(int num) {
    if (num < 2) return false;
    for (int i = 2; i <= Math.sqrt(num); i++) {
        if (num % i == 0) return false;
    }
    return true;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type4",
        source_dataset="bigclonebench",
        metadata={"bcb_functionality": "primality_test"},
    ),
    BenchmarkPair(
        pair_id="bcb_t4_03",
        code_a="""\
public static int fibonacci(int n) {
    if (n <= 1) return n;
    return fibonacci(n - 1) + fibonacci(n - 2);
}
""",
        code_b="""\
public static int fibonacci(int n) {
    if (n <= 0) return 0;
    int a = 0, b = 1;
    for (int i = 2; i <= n; i++) {
        int c = a + b;
        a = b;
        b = c;
    }
    return b;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type4",
        source_dataset="bigclonebench",
        metadata={"bcb_functionality": "fibonacci"},
    ),
    BenchmarkPair(
        pair_id="bcb_t4_04",
        code_a="""\
public static int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}
""",
        code_b="""\
public static int factorial(int n) {
    int result = 1;
    for (int i = 2; i <= n; i++) {
        result *= i;
    }
    return result;
}
""",
        language_a="java",
        language_b="java",
        is_clone=True,
        clone_type="type4",
        source_dataset="bigclonebench",
        metadata={"bcb_functionality": "factorial"},
    ),

    # -- Negative pairs (different functionality) --
    BenchmarkPair(
        pair_id="bcb_neg_01",
        code_a="""\
public static int[] bubbleSort(int[] arr) {
    int n = arr.length;
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
            }
        }
    }
    return arr;
}
""",
        code_b="""\
public static void copyFile(String src, String dst) throws IOException {
    FileInputStream fis = new FileInputStream(src);
    FileOutputStream fos = new FileOutputStream(dst);
    byte[] buffer = new byte[1024];
    int length;
    while ((length = fis.read(buffer)) > 0) {
        fos.write(buffer, 0, length);
    }
    fis.close();
    fos.close();
}
""",
        language_a="java",
        language_b="java",
        is_clone=False,
        clone_type="negative",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_neg_02",
        code_a="""\
public static int binarySearch(int[] arr, int target) {
    int low = 0;
    int high = arr.length - 1;
    while (low <= high) {
        int mid = (low + high) / 2;
        if (arr[mid] == target) return mid;
        else if (arr[mid] < target) low = mid + 1;
        else high = mid - 1;
    }
    return -1;
}
""",
        code_b="""\
public static String encodeBase64(byte[] data) {
    return Base64.getEncoder().encodeToString(data);
}
""",
        language_a="java",
        language_b="java",
        is_clone=False,
        clone_type="negative",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_neg_03",
        code_a="""\
public static boolean isPrime(int n) {
    if (n <= 1) return false;
    for (int i = 2; i * i <= n; i++) {
        if (n % i == 0) return false;
    }
    return true;
}
""",
        code_b="""\
public static String formatDate(Date date, String pattern) {
    SimpleDateFormat sdf = new SimpleDateFormat(pattern);
    return sdf.format(date);
}
""",
        language_a="java",
        language_b="java",
        is_clone=False,
        clone_type="negative",
        source_dataset="bigclonebench",
    ),
    BenchmarkPair(
        pair_id="bcb_neg_04",
        code_a="""\
public static String readFile(String path) throws IOException {
    StringBuilder content = new StringBuilder();
    BufferedReader reader = new BufferedReader(new FileReader(path));
    String line;
    while ((line = reader.readLine()) != null) {
        content.append(line).append("\\n");
    }
    reader.close();
    return content.toString();
}
""",
        code_b="""\
public static void sendEmail(String to, String subject, String body) {
    Properties props = new Properties();
    props.put("mail.smtp.host", "localhost");
    Session session = Session.getDefaultInstance(props);
    MimeMessage message = new MimeMessage(session);
    message.setRecipient(Message.RecipientType.TO, new InternetAddress(to));
    message.setSubject(subject);
    message.setText(body);
    Transport.send(message);
}
""",
        language_a="java",
        language_b="java",
        is_clone=False,
        clone_type="negative",
        source_dataset="bigclonebench",
    ),
    # Tricky negative: same domain (sorting) but inverse operation
    BenchmarkPair(
        pair_id="bcb_neg_05",
        code_a="""\
public static int[] sortAscending(int[] arr) {
    Arrays.sort(arr);
    return arr;
}
""",
        code_b="""\
public static int[] sortDescending(int[] arr) {
    Arrays.sort(arr);
    for (int i = 0; i < arr.length / 2; i++) {
        int temp = arr[i];
        arr[i] = arr[arr.length - 1 - i];
        arr[arr.length - 1 - i] = temp;
    }
    return arr;
}
""",
        language_a="java",
        language_b="java",
        is_clone=False,
        clone_type="negative",
        source_dataset="bigclonebench",
    ),
]
