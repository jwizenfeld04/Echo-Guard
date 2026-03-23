"""Ground-truth clone pairs for precision/recall validation.

Each pair has:
- Two code snippets (with language)
- A label: True (clone) or False (not a clone)
- A clone type: "type1", "type2", "type3", "type4", "cross_lang", or "negative"
- A description of what makes them similar/different

Clone type definitions (following the standard taxonomy):
- Type-1: Identical except whitespace/comments
- Type-2: Identical structure, renamed identifiers
- Type-3: Modified statements (added/removed/changed lines)
- Type-4: Same semantics, completely different implementation
- Cross-language: Same logic in different programming languages
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClonePair:
    id: str
    code_a: str
    lang_a: str
    file_a: str
    code_b: str
    lang_b: str
    file_b: str
    is_clone: bool
    clone_type: str  # "type1", "type2", "type3", "type4", "cross_lang", "negative"
    description: str


# ── Type-1: Exact clones (whitespace/comment changes only) ────────────────

TYPE1_PAIRS = [
    ClonePair(
        id="t1_01",
        code_a="""\
def binary_search(arr, target):
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
""",
        lang_a="python",
        file_a="search/binary.py",
        code_b="""\
def binary_search(arr, target):
    # Perform binary search on sorted array
    low = 0
    high = len(arr) - 1

    while low <= high:
        mid = (low + high) // 2

        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1

    return -1  # not found
""",
        lang_b="python",
        file_b="utils/search.py",
        is_clone=True,
        clone_type="type1",
        description="Identical logic, different whitespace and comments",
    ),
    ClonePair(
        id="t1_02",
        code_a="""\
def read_json_file(filepath):
    import json
    with open(filepath, 'r') as f:
        data = json.load(f)
    return data
""",
        lang_a="python",
        file_a="io/reader.py",
        code_b="""\
def read_json_file(filepath):
    import json
    # Read and parse JSON from disk
    with open(filepath, 'r') as f:
        data = json.load(f)
    return data
""",
        lang_b="python",
        file_b="helpers/file_utils.py",
        is_clone=True,
        clone_type="type1",
        description="Identical logic, only a comment added",
    ),
    ClonePair(
        id="t1_03",
        code_a="""\
def flatten_list(nested):
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(flatten_list(item))
        else:
            result.append(item)
    return result
""",
        lang_a="python",
        file_a="utils/collections.py",
        code_b="""\
def flatten_list(nested):
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(flatten_list(item))
        else:
            result.append(item)
    return result
""",
        lang_b="python",
        file_b="core/helpers.py",
        is_clone=True,
        clone_type="type1",
        description="Exact copy in a different file",
    ),
]


# ── Type-2: Renamed identifiers ──────────────────────────────────────────

TYPE2_PAIRS = [
    ClonePair(
        id="t2_01",
        code_a="""\
def binary_search(arr, target):
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
""",
        lang_a="python",
        file_a="search/binary.py",
        code_b="""\
def find_element(data, value):
    left = 0
    right = len(data) - 1
    while left <= right:
        center = (left + right) // 2
        if data[center] == value:
            return center
        elif data[center] < value:
            left = center + 1
        else:
            right = center - 1
    return -1
""",
        lang_b="python",
        file_b="utils/algo.py",
        is_clone=True,
        clone_type="type2",
        description="Same structure, all identifiers renamed",
    ),
    ClonePair(
        id="t2_02",
        code_a="""\
def validate_email(email):
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))
""",
        lang_a="python",
        file_a="auth/validation.py",
        code_b="""\
def check_email_format(addr):
    import re
    regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
    return bool(re.match(regex, addr))
""",
        lang_b="python",
        file_b="forms/validators.py",
        is_clone=True,
        clone_type="type2",
        description="Same regex validation, renamed function and variables",
    ),
    ClonePair(
        id="t2_03",
        code_a="""\
def merge_sort(items):
    if len(items) <= 1:
        return items
    mid = len(items) // 2
    left = merge_sort(items[:mid])
    right = merge_sort(items[mid:])
    return merge(left, right)

def merge(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result
""",
        lang_a="python",
        file_a="sorting/merge.py",
        code_b="""\
def sort_recursive(collection):
    if len(collection) <= 1:
        return collection
    midpoint = len(collection) // 2
    first_half = sort_recursive(collection[:midpoint])
    second_half = sort_recursive(collection[midpoint:])
    return combine(first_half, second_half)

def combine(first_half, second_half):
    output = []
    a = b = 0
    while a < len(first_half) and b < len(second_half):
        if first_half[a] <= second_half[b]:
            output.append(first_half[a])
            a += 1
        else:
            output.append(second_half[b])
            b += 1
    output.extend(first_half[a:])
    output.extend(second_half[b:])
    return output
""",
        lang_b="python",
        file_b="algo/recursive_sort.py",
        is_clone=True,
        clone_type="type2",
        description="Merge sort with all identifiers renamed",
    ),
    ClonePair(
        id="t2_04",
        code_a="""\
def hash_password(password, salt=None):
    import hashlib
    import os
    if salt is None:
        salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt + key
""",
        lang_a="python",
        file_a="auth/crypto.py",
        code_b="""\
def create_password_hash(pwd, salt_val=None):
    import hashlib
    import os
    if salt_val is None:
        salt_val = os.urandom(32)
    derived = hashlib.pbkdf2_hmac('sha256', pwd.encode('utf-8'), salt_val, 100000)
    return salt_val + derived
""",
        lang_b="python",
        file_b="security/hashing.py",
        is_clone=True,
        clone_type="type2",
        description="Password hashing with renamed identifiers",
    ),
]


# ── Type-3: Modified statements ──────────────────────────────────────────

TYPE3_PAIRS = [
    ClonePair(
        id="t3_01",
        code_a="""\
def binary_search(arr, target):
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
""",
        lang_a="python",
        file_a="search/binary.py",
        code_b="""\
def binary_search_with_count(arr, target):
    low = 0
    high = len(arr) - 1
    iterations = 0
    while low <= high:
        iterations += 1
        mid = low + (high - low) // 2
        if arr[mid] == target:
            return mid, iterations
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1, iterations
""",
        lang_b="python",
        file_b="search/instrumented.py",
        is_clone=True,
        clone_type="type3",
        description="Binary search with added iteration counter and overflow-safe midpoint",
    ),
    ClonePair(
        id="t3_02",
        code_a="""\
def read_csv(filepath, delimiter=','):
    rows = []
    with open(filepath, 'r') as f:
        header = f.readline().strip().split(delimiter)
        for line in f:
            values = line.strip().split(delimiter)
            rows.append(dict(zip(header, values)))
    return rows
""",
        lang_a="python",
        file_a="io/csv_reader.py",
        code_b="""\
def parse_csv(filepath, separator=',', skip_empty=True):
    records = []
    with open(filepath, 'r', encoding='utf-8') as f:
        columns = f.readline().strip().split(separator)
        for line in f:
            line = line.strip()
            if skip_empty and not line:
                continue
            fields = line.split(separator)
            if len(fields) == len(columns):
                records.append(dict(zip(columns, fields)))
    return records
""",
        lang_b="python",
        file_b="parsers/csv_parser.py",
        is_clone=True,
        clone_type="type3",
        description="CSV reader with added empty-line skip and length validation",
    ),
    ClonePair(
        id="t3_03",
        code_a="""\
def retry_request(url, max_retries=3):
    import time
    import requests
    for attempt in range(max_retries):
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
""",
        lang_a="python",
        file_a="http/client.py",
        code_b="""\
def fetch_with_retry(endpoint, retries=3, timeout=10):
    import time
    import requests
    for i in range(retries):
        try:
            resp = requests.get(endpoint, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            if i == retries - 1:
                raise RuntimeError(f"Failed after {retries} attempts") from exc
            wait = 2 ** i
            time.sleep(wait)
""",
        lang_b="python",
        file_b="api/helpers.py",
        is_clone=True,
        clone_type="type3",
        description="HTTP retry with added timeout, broader exception handling, and custom error",
    ),
]


# ── Type-4: Same semantics, different implementation ─────────────────────

TYPE4_PAIRS = [
    ClonePair(
        id="t4_01",
        code_a="""\
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)
""",
        lang_a="python",
        file_a="math/recursive.py",
        code_b="""\
def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
""",
        lang_b="python",
        file_b="math/iterative.py",
        is_clone=True,
        clone_type="type4",
        description="Fibonacci: recursive vs iterative — same output, different algorithm",
    ),
    ClonePair(
        id="t4_02",
        code_a="""\
def unique_elements(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
""",
        lang_a="python",
        file_a="utils/dedup.py",
        code_b="""\
def unique_elements(items):
    return list(dict.fromkeys(items))
""",
        lang_b="python",
        file_b="utils/compact.py",
        is_clone=True,
        clone_type="type4",
        description="Deduplication: manual set tracking vs dict.fromkeys — same semantics",
    ),
    ClonePair(
        id="t4_03",
        code_a="""\
def is_palindrome(s):
    cleaned = ''.join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]
""",
        lang_a="python",
        file_a="strings/checks.py",
        code_b="""\
def is_palindrome(text):
    cleaned = ''.join(c.lower() for c in text if c.isalnum())
    left = 0
    right = len(cleaned) - 1
    while left < right:
        if cleaned[left] != cleaned[right]:
            return False
        left += 1
        right -= 1
    return True
""",
        lang_b="python",
        file_b="strings/two_pointer.py",
        is_clone=True,
        clone_type="type4",
        description="Palindrome check: slice reversal vs two-pointer — same semantics",
    ),
    ClonePair(
        id="t4_04",
        code_a="""\
def flatten_list(nested):
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(flatten_list(item))
        else:
            result.append(item)
    return result
""",
        lang_a="python",
        file_a="utils/collections.py",
        code_b="""\
def flatten_list(nested):
    result = []
    stack = list(nested)
    while stack:
        item = stack.pop(0)
        if isinstance(item, list):
            stack = item + stack
        else:
            result.append(item)
    return result
""",
        lang_b="python",
        file_b="utils/iterative.py",
        is_clone=True,
        clone_type="type4",
        description="Flatten: recursive vs iterative stack — same semantics",
    ),
]


# ── Cross-language: Same logic in different languages ────────────────────

CROSS_LANG_PAIRS = [
    ClonePair(
        id="xl_01",
        code_a="""\
def binary_search(arr, target):
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
""",
        lang_a="python",
        file_a="search.py",
        code_b="""\
function binarySearch(arr, target) {
    let low = 0;
    let high = arr.length - 1;
    while (low <= high) {
        const mid = Math.floor((low + high) / 2);
        if (arr[mid] === target) {
            return mid;
        } else if (arr[mid] < target) {
            low = mid + 1;
        } else {
            high = mid - 1;
        }
    }
    return -1;
}
""",
        lang_b="javascript",
        file_b="search.js",
        is_clone=True,
        clone_type="cross_lang",
        description="Binary search: Python vs JavaScript",
    ),
    ClonePair(
        id="xl_02",
        code_a="""\
def hash_password(password, salt=None):
    import hashlib
    import os
    if salt is None:
        salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt + key
""",
        lang_a="python",
        file_a="auth/crypto.py",
        code_b="""\
func hashPassword(password string, salt []byte) ([]byte, error) {
    if salt == nil {
        salt = make([]byte, 32)
        if _, err := rand.Read(salt); err != nil {
            return nil, err
        }
    }
    key := pbkdf2.Key([]byte(password), salt, 100000, 32, sha256.New)
    return append(salt, key...), nil
}
""",
        lang_b="go",
        file_b="auth/crypto.go",
        is_clone=True,
        clone_type="cross_lang",
        description="Password hashing: Python vs Go",
    ),
    ClonePair(
        id="xl_03",
        code_a="""\
def retry_request(url, max_retries=3):
    import time
    import requests
    for attempt in range(max_retries):
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
""",
        lang_a="python",
        file_a="http/client.py",
        code_b="""\
async function retryRequest(url, maxRetries = 3) {
    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (err) {
            if (attempt === maxRetries - 1) throw err;
            await new Promise(r => setTimeout(r, 2 ** attempt * 1000));
        }
    }
}
""",
        lang_b="javascript",
        file_b="http/client.js",
        is_clone=True,
        clone_type="cross_lang",
        description="HTTP retry with exponential backoff: Python vs JavaScript",
    ),
    ClonePair(
        id="xl_04",
        code_a="""\
def merge_sort(items):
    if len(items) <= 1:
        return items
    mid = len(items) // 2
    left = merge_sort(items[:mid])
    right = merge_sort(items[mid:])
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result
""",
        lang_a="python",
        file_a="sort/merge.py",
        code_b="""\
public static int[] mergeSort(int[] items) {
    if (items.length <= 1) return items;
    int mid = items.length / 2;
    int[] left = mergeSort(Arrays.copyOfRange(items, 0, mid));
    int[] right = mergeSort(Arrays.copyOfRange(items, mid, items.length));
    int[] result = new int[items.length];
    int i = 0, j = 0, k = 0;
    while (i < left.length && j < right.length) {
        if (left[i] <= right[j]) {
            result[k++] = left[i++];
        } else {
            result[k++] = right[j++];
        }
    }
    while (i < left.length) result[k++] = left[i++];
    while (j < right.length) result[k++] = right[j++];
    return result;
}
""",
        lang_b="java",
        file_b="Sort.java",
        is_clone=True,
        clone_type="cross_lang",
        description="Merge sort: Python vs Java",
    ),
]


# ── True negatives: Functions that should NOT match ──────────────────────

NEGATIVE_PAIRS = [
    ClonePair(
        id="neg_01",
        code_a="""\
def binary_search(arr, target):
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
""",
        lang_a="python",
        file_a="search/binary.py",
        code_b="""\
def send_email(to_address, subject, body, smtp_host='localhost'):
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['To'] = to_address
    msg['From'] = 'noreply@example.com'
    with smtplib.SMTP(smtp_host) as server:
        server.send_message(msg)
    return True
""",
        lang_b="python",
        file_b="notifications/email.py",
        is_clone=False,
        clone_type="negative",
        description="Binary search vs email sending — completely unrelated",
    ),
    ClonePair(
        id="neg_02",
        code_a="""\
def hash_password(password, salt=None):
    import hashlib
    import os
    if salt is None:
        salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt + key
""",
        lang_a="python",
        file_a="auth/crypto.py",
        code_b="""\
def render_template(template_name, context):
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template(template_name)
    return template.render(**context)
""",
        lang_b="python",
        file_b="web/views.py",
        is_clone=False,
        clone_type="negative",
        description="Password hashing vs template rendering — unrelated",
    ),
    ClonePair(
        id="neg_03",
        code_a="""\
def read_csv(filepath, delimiter=','):
    rows = []
    with open(filepath, 'r') as f:
        header = f.readline().strip().split(delimiter)
        for line in f:
            values = line.strip().split(delimiter)
            rows.append(dict(zip(header, values)))
    return rows
""",
        lang_a="python",
        file_a="io/csv_reader.py",
        code_b="""\
def connect_database(host, port, dbname, user, password):
    import psycopg2
    conn = psycopg2.connect(
        host=host, port=port, dbname=dbname,
        user=user, password=password,
    )
    conn.autocommit = False
    return conn
""",
        lang_b="python",
        file_b="db/connection.py",
        is_clone=False,
        clone_type="negative",
        description="CSV reader vs database connection — unrelated",
    ),
    ClonePair(
        id="neg_04",
        code_a="""\
def validate_email(email):
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))
""",
        lang_a="python",
        file_a="auth/validation.py",
        code_b="""\
def compress_image(input_path, output_path, quality=85):
    from PIL import Image
    img = Image.open(input_path)
    img.save(output_path, optimize=True, quality=quality)
    return output_path
""",
        lang_b="python",
        file_b="media/images.py",
        is_clone=False,
        clone_type="negative",
        description="Email validation vs image compression — unrelated",
    ),
    ClonePair(
        id="neg_05",
        code_a="""\
def flatten_list(nested):
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(flatten_list(item))
        else:
            result.append(item)
    return result
""",
        lang_a="python",
        file_a="utils/collections.py",
        code_b="""\
def create_thumbnail(image_path, size=(128, 128)):
    from PIL import Image
    img = Image.open(image_path)
    img.thumbnail(size)
    thumb_path = image_path.replace('.', '_thumb.')
    img.save(thumb_path)
    return thumb_path
""",
        lang_b="python",
        file_b="media/thumbs.py",
        is_clone=False,
        clone_type="negative",
        description="List flattening vs thumbnail creation — unrelated",
    ),
    # Tricky negatives: similar tokens but different semantics
    ClonePair(
        id="neg_06",
        code_a="""\
def parse_url(url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        'scheme': parsed.scheme,
        'host': parsed.hostname,
        'port': parsed.port,
        'path': parsed.path,
        'query': parsed.query,
    }
""",
        lang_a="python",
        file_a="http/url.py",
        code_b="""\
def build_url(scheme, host, port, path, query=None):
    from urllib.parse import urlencode
    base = f"{scheme}://{host}"
    if port:
        base += f":{port}"
    base += path
    if query:
        base += '?' + urlencode(query)
    return base
""",
        lang_b="python",
        file_b="http/builder.py",
        is_clone=False,
        clone_type="negative",
        description="URL parsing vs URL building — same domain, opposite operations",
    ),
    ClonePair(
        id="neg_07",
        code_a="""\
def encrypt_data(plaintext, key):
    from cryptography.fernet import Fernet
    cipher = Fernet(key)
    return cipher.encrypt(plaintext.encode())
""",
        lang_a="python",
        file_a="security/encrypt.py",
        code_b="""\
def decrypt_data(ciphertext, key):
    from cryptography.fernet import Fernet
    cipher = Fernet(key)
    return cipher.decrypt(ciphertext).decode()
""",
        lang_b="python",
        file_b="security/decrypt.py",
        is_clone=False,
        clone_type="negative",
        description="Encrypt vs decrypt — similar structure but inverse operations",
    ),
]


def get_all_pairs() -> list[ClonePair]:
    """Return all ground-truth pairs."""
    return (
        TYPE1_PAIRS
        + TYPE2_PAIRS
        + TYPE3_PAIRS
        + TYPE4_PAIRS
        + CROSS_LANG_PAIRS
        + NEGATIVE_PAIRS
    )


def get_pairs_by_type(clone_type: str) -> list[ClonePair]:
    """Return pairs of a specific clone type."""
    return [p for p in get_all_pairs() if p.clone_type == clone_type]
