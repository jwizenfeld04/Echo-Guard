# Type-4 Clone Detection: Why 69.5% on GPTCloneBench but 0% on BigCloneBench?

## The Short Answer

GPTCloneBench and BigCloneBench label completely different things as "Type-4 clones":

| Property | GPTCloneBench Type-4 | BigCloneBench Type-4 |
|---|---|---|
| **Who wrote it** | GPT-3/GPT-4 | Human developers |
| **How it was created** | "Rewrite this function differently" | Independent implementations |
| **Shared vocabulary** | High (same seed function) | Near zero |
| **Shared structure** | Moderate (same algorithmic approach) | None |
| **Embedding score range** | **0.85-0.97** | **0.56-0.75** |
| **Above threshold (0.81)?** | Most (69.5%) | No (0%) |

These aren't the same kind of clone. GPTCloneBench Type-4 pairs are **AI-generated variations** that retain vocabulary and structure from the original. BigCloneBench Type-4 pairs are **independently written implementations** that share almost nothing syntactically.

---

## Detailed Analysis

### GPTCloneBench Type-4: AI-Generated Variations (Score: 0.90-0.97)

GPTCloneBench was built by giving GPT-3/GPT-4 a seed function and asking it to "implement the same functionality using a different approach." The AI rewrites the code but retains:

- The same variable naming patterns (data, result, output)
- Similar API calls (the same standard library functions)
- Comparable control flow (loops over the same data structures)
- Identical data literals (the same test arrays, constants)

**Example — GCB Type-4 pair (score: 0.97)**

Function A:
```java
public static void main(String[] args) {
    double[][] data = {{97, 36, 79}, {94, 74, 60}, ...};
    double data1[] = new double[data.length];
    double data2[] = new double[data.length];
    // Processes columns individually
    for (int i = 0; i < data.length; i++) {
        data1[i] = data[i][0];
    }
    // ... computes statistics per column
}
```

Function B:
```java
public static void main(String[] args) {
    double[][] data = {{97, 36, 79}, {94, 74, 60}, ...};
    int columns = data[0].length;
    double dataArray[] = new double[data.length * columns];
    // Flattens into 1D array
    for (int i = 0; i < data.length; i++) {
        for (int j = 0; j < columns; j++) {
            dataArray[i * columns + j] = data[i][j];
        }
    }
    // ... computes statistics from flat array
}
```

**Why embeddings detect this**: Both functions share the same data literals (`{{97, 36, 79}, ...}`), the same variable types (`double[][]`), and similar loop structures. The GPT-generated "alternative implementation" reorganized the computation but kept 70%+ of the tokens intact. UniXcoder sees these as nearly identical code.

**This is realistic for AI agents** — when Claude, Cursor, or Copilot generates a "different" implementation, it typically produces exactly this kind of clone: structurally rearranged but vocabulary-heavy overlap.

---

### BigCloneBench Type-4: Independent Human Implementations (Score: 0.56-0.75)

BigCloneBench Type-4 pairs are independently written Java functions that happen to implement the same *functionality* (e.g., "download a URL" or "copy a file") but were written by different developers at different times with no shared context.

**Example — BCB Type-4 pair (score: 0.75, highest in sample)**

Function A:
```java
public void run() {
    Vector<Update> updates = new Vector<Update>();
    if (dic != null) updates.add(dic);
    if (gen != null) updates.add(gen);
    if (res != null) updates.add(res);
    if (help != null) updates.add(help);
    for (Iterator iterator = updates.iterator(); iterator.hasNext(); ) {
        Update update = (Update) iterator.next();
        try { update.execute(); }
        catch (Exception e) { ... }
    }
}
```

Function B:
```java
public void doGet(HttpServletRequest request, HttpServletResponse response)
    throws ServletException, IOException {
    String selectedPage = request.getParameter("SelectedPage");
    Page page = null;
    PortalRequest portalRequest = PortalRequest.getCurrentRequest();
    if (selectedPage == null) {
        Property pageProp = Property.getProperty("Homepage", ...);
    }
    // ... completely different web framework code
}
```

**Why embeddings miss this**: These functions share zero vocabulary. One uses `Vector<Update>`, `Iterator`, `execute()`. The other uses `HttpServletRequest`, `getParameter()`, `PortalRequest`. They both "process items" at a high semantic level, but the code has nothing in common. UniXcoder can't bridge this gap without fine-tuning on clone detection specifically.

**Example — BCB Type-4 pair (score: 0.56, lowest in sample)**

Function A (6 lines):
```java
public static String stringOfUrl(String addr) throws IOException {
    ByteArrayOutputStream output = new ByteArrayOutputStream();
    URL url = new URL(addr);
    IOUtils.copy(url.openStream(), output);
    return output.toString();
}
```

Function B (20+ lines):
```java
public void doGet(HttpServletRequest request, HttpServletResponse response)
    throws ServletException, IOException {
    String selectedPage = request.getParameter("SelectedPage");
    Page page = null;
    PortalRequest portalRequest = PortalRequest.getCurrentRequest();
    // ... completely different web framework code
}
```

Both are labeled as implementing the same "functionality" in BigCloneBench, but one is a URL fetcher and the other is a servlet handler. They share the word "IOException" and nothing else.

---

### POJ-104 Type-4: Same Problem, Different Algorithms (Score: 0.68-0.98)

POJ-104 sits between the two extremes. These are competitive programming solutions — different students solving the same problem, often with similar algorithmic approaches but different coding styles.

**Example — POJ-104 Type-4 DETECTED (score: 0.98)**

Both solutions sort males and females separately from input:

Function A:
```c
int main(){
    int n,i,t,p,q,j;
    double a[40],c[40],d[40],e;
    char b[40][6];
    p=0; q=0;
    scanf("%d",&n);
    for(i=0;i<n;i++){
        scanf("%s %lf",b[i],&a[i]);
        t=strcmp(b[i],"female");
        if(t==0){ d[q]=a[i]; q++; }
        else{ c[p]=a[i]; p++; }
    }
    // ... bubble sort both arrays
}
```

Function B:
```c
int main(){
    int n,i,k=0,j=0,b;
    float a,e,m[40],f[40];
    char p[10];
    scanf("%d",&n);
    for(i=0;i<n;i++){
        scanf("%s",&p);
        scanf("%f",&a);
        if(p[0]=='f'){ f[k]=a; k++; }
        else{ m[j]=a; j++; }
    }
    // ... bubble sort both arrays
}
```

**Why detected**: Same algorithm (read→split by gender→sort→output), same C idioms (`scanf`, arrays, nested for loops), same structure. Despite different variable names, UniXcoder encodes the structural pattern and API usage.

**Example — POJ-104 Type-4 MISSED (score: 0.68)**

Function A: Brute-force triple nested loop
```c
int main() {
    int A, B, C, a, b, c;
    for (A = 1; A <= 3; A++)
        for (B = 1; B <= 3; B++)
            for (C = 1; C <= 3; C++) {
                a = (B > A) + (C == A);
                // ... constraint satisfaction
            }
}
```

Function B: Completely different approach with array-based logic (not shown in full)

**Why missed**: Different algorithmic approach to the same problem. One uses brute-force enumeration, the other uses a different strategy. No shared structure.

---

## The Spectrum of "Type-4"

Type-4 is not a single category — it's a spectrum:

```text
GPT Clone          POJ-104 (similar algo)    POJ-104 (different algo)    BCB Type-4
  |                        |                          |                      |
  v                        v                          v                      v
0.97                     0.98                       0.68                   0.56-0.75
DETECTED                DETECTED                   MISSED                  MISSED
  |                        |                          |                      |
Same vocabulary      Same structure              Different structure    Different everything
Different arrangement  Different variables        Same problem           Same "functionality"
```

| Sub-type | What's shared | Embedding score | Detection |
|---|---|---|---|
| AI-generated variation | Vocabulary + partial structure | 0.90-0.97 | **Detected** |
| Same algorithm, different style | Structure + idioms | 0.85-0.98 | **Usually detected** |
| Same problem, different algorithm | Intent only | 0.65-0.85 | **Sometimes detected** |
| Same functionality, independent code | Abstract concept only | 0.55-0.75 | **Not detected** |

---

## What This Means for Echo Guard Users

Echo Guard's Type-4 detection is calibrated for the **most common real-world scenario**: AI agents generating code that already exists in your codebase. This is the GPTCloneBench case — and we detect **69.5%** of those (with 100% precision on detected pairs).

The BigCloneBench Type-4 gap (0%) is a known limitation of zero-shot embeddings. Detecting that `stringOfUrl()` and `doGet()` implement the same "functionality" requires:
1. **Contrastive fine-tuning** of the embedding model on known-equivalent pairs (see [SEMANTIC-DETECTION-RESEARCH.md](SEMANTIC-DETECTION-RESEARCH.md))
2. **Execution-based validation** — running both functions on the same inputs to compare outputs
3. **Semantic understanding** beyond structural similarity (understanding what code *does*, not what it *looks like*)

The GPTCloneBench recall dropped from earlier reported numbers due to the three-tier pipeline's classifier and intent filters being more aggressive at filtering borderline matches. The 69.5% recall with 100% precision is a better trade-off for real-world usage than higher recall with more false positives.

For AI-assisted development, where duplicates are generated by the same LLM that produced similar code moments ago, the current detection catches the majority of echoes. For legacy codebase auditing where different developers independently wrote the same logic years apart, improved embeddings or execution-based approaches would be needed.

---

## References

- [BigCloneBench mislabeling critique](https://arxiv.org/abs/2505.04311) — Krinke et al. (2025) found **93% of WT3/T4 pairs in BigCloneBench are mislabeled** (not actually functionally similar)
- [UniXcoder](https://aclanthology.org/2022.acl-long.499.pdf) — Guo et al., ACL 2022
- [GPTCloneBench](https://arxiv.org/abs/2308.13963) — Alam et al., ICSME 2023
- [POJ-104 / CodeXGLUE](https://arxiv.org/abs/2102.04664) — Lu et al., NeurIPS 2021
