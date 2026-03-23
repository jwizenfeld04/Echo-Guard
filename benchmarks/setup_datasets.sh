#!/usr/bin/env bash
#
# Setup benchmark datasets for Echo Guard evaluation.
#
# Usage:
#   ./benchmarks/setup_datasets.sh bigclonebench    # Setup BigCloneBench
#   ./benchmarks/setup_datasets.sh all               # Setup all datasets
#
# Prerequisites:
#   - Java 11+ (for H2 database export)
#   - BigCloneBench: download the two tar.gz files manually (OneDrive links)
#     and place them in benchmarks/data/bigclonebench/
#
# See benchmarks/SETUP.md for full instructions.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/data"

H2_VERSION="1.4.200"
H2_JAR_URL="https://repo1.maven.org/maven2/com/h2database/h2/${H2_VERSION}/h2-${H2_VERSION}.jar"

# ── Helpers ──────────────────────────────────────────────────────────────

log()  { echo "  [*] $*"; }
ok()   { echo "  [+] $*"; }
err()  { echo "  [!] $*" >&2; }
die()  { err "$@"; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed"
}

# ── BigCloneBench ────────────────────────────────────────────────────────

setup_bigclonebench() {
    local bcb_dir="${DATA_DIR}/bigclonebench"
    local h2_jar="${bcb_dir}/h2.jar"

    echo ""
    echo "=========================================="
    echo "  BigCloneBench Setup"
    echo "=========================================="
    echo ""

    require_cmd java

    # Check tar files exist
    local bcb_tar="${bcb_dir}/BigCloneBench_BCEvalVersion.tar.gz"
    local ija_tar="${bcb_dir}/IJaDataset_BCEvalVersion.tar.gz"

    if [[ ! -f "$bcb_tar" ]]; then
        die "Missing: ${bcb_tar}
  Download from: https://1drv.ms/u/s!AhXbM6MKt_yLj_NwwVacvUzmi6uorA?e=eMu0P4
  Place the file in: ${bcb_dir}/"
    fi

    if [[ ! -f "$ija_tar" ]]; then
        die "Missing: ${ija_tar}
  Download from: https://1drv.ms/u/s!AhXbM6MKt_yLj_N15CewgjM7Y8NLKA?e=cScoRJ
  Place the file in: ${bcb_dir}/"
    fi

    # Step 1: Extract archives
    if [[ ! -f "${bcb_dir}/bcb.h2.db" ]]; then
        log "Extracting BigCloneBench database..."
        tar -xzf "$bcb_tar" -C "$bcb_dir"
        ok "Database extracted ($(du -sh "${bcb_dir}/bcb.h2.db" | cut -f1))"
    else
        ok "Database already extracted"
    fi

    if [[ ! -d "${bcb_dir}/bcb_reduced" ]]; then
        log "Extracting IJaDataset..."
        tar -xzf "$ija_tar" -C "$bcb_dir"
        ok "IJaDataset extracted ($(ls "${bcb_dir}/bcb_reduced" | wc -l | tr -d ' ') functionality dirs)"
    else
        ok "IJaDataset already extracted"
    fi

    # Step 2: Download H2 jar if needed
    if [[ ! -f "$h2_jar" ]]; then
        log "Downloading H2 database driver v${H2_VERSION}..."
        curl -sL -o "$h2_jar" "$H2_JAR_URL"
        ok "H2 driver downloaded ($(du -sh "$h2_jar" | cut -f1))"
    fi

    # Step 3: Export clone pairs to CSV
    local h2_url="jdbc:h2:${bcb_dir}/bcb;IFEXISTS=TRUE"

    if [[ ! -f "${bcb_dir}/clonepairs.csv" ]]; then
        log "Exporting stratified clone pairs (200 per type)..."
        java -cp "$h2_jar" org.h2.tools.Shell \
            -url "$h2_url" -user "sa" -password "" \
            -sql "CALL CSVWRITE('${bcb_dir}/clonepairs.csv', '
SELECT * FROM (
  (SELECT FUNCTION_ID_ONE, FUNCTION_ID_TWO, FUNCTIONALITY_ID, SYNTACTIC_TYPE, SIMILARITY_LINE, SIMILARITY_TOKEN FROM CLONES WHERE SYNTACTIC_TYPE = 1 LIMIT 200)
  UNION ALL
  (SELECT FUNCTION_ID_ONE, FUNCTION_ID_TWO, FUNCTIONALITY_ID, SYNTACTIC_TYPE, SIMILARITY_LINE, SIMILARITY_TOKEN FROM CLONES WHERE SYNTACTIC_TYPE = 2 LIMIT 200)
  UNION ALL
  (SELECT FUNCTION_ID_ONE, FUNCTION_ID_TWO, FUNCTIONALITY_ID, SYNTACTIC_TYPE, SIMILARITY_LINE, SIMILARITY_TOKEN FROM CLONES WHERE SYNTACTIC_TYPE = 3 AND SIMILARITY_TOKEN >= 0.7 LIMIT 200)
  UNION ALL
  (SELECT FUNCTION_ID_ONE, FUNCTION_ID_TWO, FUNCTIONALITY_ID, SYNTACTIC_TYPE, SIMILARITY_LINE, SIMILARITY_TOKEN FROM CLONES WHERE SYNTACTIC_TYPE = 3 AND SIMILARITY_TOKEN >= 0.5 AND SIMILARITY_TOKEN < 0.7 LIMIT 200)
  UNION ALL
  (SELECT FUNCTION_ID_ONE, FUNCTION_ID_TWO, FUNCTIONALITY_ID, SYNTACTIC_TYPE, SIMILARITY_LINE, SIMILARITY_TOKEN FROM CLONES WHERE SYNTACTIC_TYPE = 3 AND SIMILARITY_TOKEN < 0.5 LIMIT 200)
)
')" > /dev/null 2>&1
        ok "Exported $(wc -l < "${bcb_dir}/clonepairs.csv" | tr -d ' ') clone pairs"
    else
        ok "Clone pairs CSV already exists"
    fi

    # Step 4: Export false positives (negative pairs)
    if [[ ! -f "${bcb_dir}/false_positives.csv" ]]; then
        log "Exporting false positive pairs (200 negatives)..."
        java -cp "$h2_jar" org.h2.tools.Shell \
            -url "$h2_url" -user "sa" -password "" \
            -sql "CALL CSVWRITE('${bcb_dir}/false_positives.csv', 'SELECT FUNCTION_ID_ONE, FUNCTION_ID_TWO, FUNCTIONALITY_ID FROM FALSE_POSITIVES LIMIT 200')" > /dev/null 2>&1
        ok "Exported $(wc -l < "${bcb_dir}/false_positives.csv" | tr -d ' ') false positive pairs"
    else
        ok "False positives CSV already exists"
    fi

    # Step 5: Export functions index
    if [[ ! -f "${bcb_dir}/functions.csv" ]]; then
        log "Exporting function index (22M entries, takes ~60s)..."
        java -cp "$h2_jar" org.h2.tools.Shell \
            -url "$h2_url" -user "sa" -password "" \
            -sql "CALL CSVWRITE('${bcb_dir}/functions.csv', 'SELECT ID, NAME, TYPE, STARTLINE, ENDLINE, NORMALIZED_SIZE, TOKENS FROM FUNCTIONS')" > /dev/null 2>&1
        ok "Exported functions index ($(du -sh "${bcb_dir}/functions.csv" | cut -f1))"
    else
        ok "Functions CSV already exists"
    fi

    # Step 6: Clean up
    log "Cleaning up..."
    rm -f "$h2_jar"
    rm -f "$bcb_tar" "$ija_tar"
    rm -f "${bcb_dir}/bcb.trace.db"
    ok "Removed tar files and H2 jar"

    echo ""
    echo "=========================================="
    echo "  BigCloneBench setup complete!"
    echo "=========================================="
    echo ""
    echo "  Clone pairs:      $(wc -l < "${bcb_dir}/clonepairs.csv" | tr -d ' ') rows"
    echo "  False positives:  $(wc -l < "${bcb_dir}/false_positives.csv" | tr -d ' ') rows"
    echo "  Functions index:  $(du -sh "${bcb_dir}/functions.csv" | cut -f1)"
    echo "  Source files:     $(ls "${bcb_dir}/bcb_reduced" | wc -l | tr -d ' ') functionality dirs"
    echo ""
    echo "  Run the benchmark:"
    echo "    python -m benchmarks.runner --dataset bigclonebench --verbose"
    echo ""
    echo "  Estimated: ~7 min, ~6.2 GB RAM"
    echo ""
}

# ── GPTCloneBench ────────────────────────────────────────────────────────

setup_gptclonebench() {
    local gcb_dir="${DATA_DIR}/gptclonebench"

    echo ""
    echo "=========================================="
    echo "  GPTCloneBench Setup"
    echo "=========================================="
    echo ""

    require_cmd git
    require_cmd unzip

    mkdir -p "$gcb_dir"

    # Check for the standalone clones zip
    local standalone_zip="${gcb_dir}/GPTCloneBench_semantic_standalone_clones.zip"

    if [[ ! -f "$standalone_zip" ]]; then
        # Try to clone the repo and get the zip
        local repo_dir="${gcb_dir}/_repo"
        if [[ ! -d "$repo_dir" ]]; then
            log "Cloning GPTCloneBench repository..."
            git clone --depth 1 https://github.com/srlabUsask/GPTCloneBench.git "$repo_dir" 2>&1 | tail -1
            ok "Repository cloned"
        fi

        # Find the standalone zip in the repo
        local found_zip
        found_zip=$(find "$repo_dir" -name "GPTCloneBench_semantic_standalone_clones.zip" -type f 2>/dev/null | head -1)
        if [[ -n "$found_zip" ]]; then
            cp "$found_zip" "$standalone_zip"
            ok "Found standalone clones zip in repo"
        else
            # The zip might need to be downloaded from Zenodo
            err "Standalone clones zip not found in repo."
            echo "  Try downloading from Zenodo: https://doi.org/10.5281/zenodo.10198952"
            echo "  Place GPTCloneBench_semantic_standalone_clones.zip in: ${gcb_dir}/"
            echo ""
            echo "  Alternatively, you can use the clone pairs directly from the repo."
            echo "  Checking for extracted data in repo..."
        fi
    fi

    # Extract the zip if we have it
    if [[ -f "$standalone_zip" ]]; then
        log "Extracting standalone semantic clones..."
        unzip -qo "$standalone_zip" -d "$gcb_dir"
        ok "Extracted"
    fi

    # Also check if the repo has pre-extracted directories
    local repo_dir="${gcb_dir}/_repo"
    if [[ -d "$repo_dir" ]]; then
        # Copy any *_similar_distinctive directories from repo
        for dir in "$repo_dir"/*_similar_distinctive "$repo_dir"/*_leq_*; do
            if [[ -d "$dir" ]]; then
                local dirname
                dirname=$(basename "$dir")
                if [[ ! -d "${gcb_dir}/${dirname}" ]]; then
                    log "Copying ${dirname}..."
                    cp -r "$dir" "${gcb_dir}/${dirname}"
                fi
            fi
        done
    fi

    # Count what we have
    local t3_count=0
    local t4_count=0
    for dir in "${gcb_dir}"/*_51_to_75_similar_distinctive; do
        [[ -d "$dir" ]] && t3_count=$(( t3_count + $(find "$dir" -type f | wc -l | tr -d ' ') ))
    done
    for dir in "${gcb_dir}"/*_leq_50_similar_distinctive; do
        [[ -d "$dir" ]] && t4_count=$(( t4_count + $(find "$dir" -type f | wc -l | tr -d ' ') ))
    done

    # Clean up
    log "Cleaning up..."
    rm -rf "${gcb_dir}/_repo"
    rm -f "$standalone_zip"
    ok "Removed temp files"

    echo ""
    echo "=========================================="
    echo "  GPTCloneBench setup complete!"
    echo "=========================================="
    echo ""
    echo "  Type-3 clone files: ${t3_count}"
    echo "  Type-4 clone files: ${t4_count}"
    echo ""
    echo "  Run the benchmark:"
    echo "    python -m benchmarks.runner --dataset gptclonebench --verbose"
    echo ""
    echo "  Estimated: ~2 min, ~500 MB RAM"
    echo ""
}

# ── POJ-104 ──────────────────────────────────────────────────────────────

setup_poj104() {
    local poj_dir="${DATA_DIR}/poj104"

    echo ""
    echo "=========================================="
    echo "  POJ-104 Setup"
    echo "=========================================="
    echo ""

    mkdir -p "$poj_dir"

    local tar_file="${poj_dir}/programs.tar.gz"

    # Check for the tar file or already extracted data
    if [[ -d "${poj_dir}/ProgramData" ]]; then
        ok "ProgramData already extracted"
    elif [[ -f "$tar_file" ]]; then
        log "Extracting programs.tar.gz..."
        tar -xzf "$tar_file" -C "$poj_dir"
        ok "Extracted"
    else
        # Try downloading with gdown (Google Drive)
        if command -v gdown >/dev/null 2>&1; then
            log "Downloading POJ-104 dataset from Google Drive..."
            gdown "https://drive.google.com/uc?id=0B2i-vWnOu7MxVlJwQXN6eVNONUU" -O "$tar_file" 2>&1 | tail -3
            log "Extracting programs.tar.gz..."
            tar -xzf "$tar_file" -C "$poj_dir"
            ok "Downloaded and extracted"
        else
            # Try downloading the preprocessed JSONL from CodeXGLUE
            log "gdown not found, trying CodeXGLUE preprocessed data..."
            local codexglue_dir="${poj_dir}/_codexglue"
            if [[ ! -d "$codexglue_dir" ]]; then
                git clone --depth 1 --filter=blob:none --sparse \
                    https://github.com/microsoft/CodeXGLUE.git "$codexglue_dir" 2>&1 | tail -1
                cd "$codexglue_dir"
                git sparse-checkout set Code-Code/Clone-detection-POJ-104/dataset 2>/dev/null || true
                cd - > /dev/null
            fi

            # Copy JSONL files if they exist
            local dataset_dir="${codexglue_dir}/Code-Code/Clone-detection-POJ-104/dataset"
            if [[ -d "$dataset_dir" ]]; then
                for f in train.jsonl valid.jsonl test.jsonl; do
                    [[ -f "${dataset_dir}/${f}" ]] && cp "${dataset_dir}/${f}" "${poj_dir}/${f}"
                done
                ok "Copied JSONL files from CodeXGLUE"
            else
                err "Could not download POJ-104 dataset automatically."
                echo ""
                echo "  Option 1: Install gdown and re-run:"
                echo "    pip install gdown"
                echo "    ./benchmarks/setup_datasets.sh poj104"
                echo ""
                echo "  Option 2: Download manually from Google Drive:"
                echo "    https://drive.google.com/file/d/0B2i-vWnOu7MxVlJwQXN6eVNONUU/view"
                echo "    Place programs.tar.gz in: ${poj_dir}/"
                echo "    Re-run this script."
                echo ""
                rm -rf "$codexglue_dir"
                return 1
            fi
            rm -rf "$codexglue_dir"
        fi
    fi

    # Count what we have
    local prog_count=0
    local problem_count=0
    if [[ -d "${poj_dir}/ProgramData" ]]; then
        problem_count=$(ls "${poj_dir}/ProgramData" | wc -l | tr -d ' ')
        prog_count=$(find "${poj_dir}/ProgramData" -type f | wc -l | tr -d ' ')
    elif [[ -f "${poj_dir}/test.jsonl" ]]; then
        prog_count=$(wc -l < "${poj_dir}/test.jsonl" | tr -d ' ')
        problem_count="(from JSONL)"
    fi

    # Clean up
    rm -f "$tar_file"

    echo ""
    echo "=========================================="
    echo "  POJ-104 setup complete!"
    echo "=========================================="
    echo ""
    echo "  Problems:  ${problem_count}"
    echo "  Solutions: ${prog_count}"
    echo ""
    echo "  Run the benchmark:"
    echo "    python -m benchmarks.runner --dataset poj104 --verbose"
    echo ""
    echo "  Estimated: ~5 min, ~2 GB RAM"
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────

usage() {
    echo "Usage: $0 <dataset>"
    echo ""
    echo "Datasets:"
    echo "  bigclonebench   Setup BigCloneBench (requires manual download first)"
    echo "  gptclonebench   Setup GPTCloneBench (cloned from GitHub)"
    echo "  poj104          Setup POJ-104 (downloaded from Google Drive or CodeXGLUE)"
    echo "  all             Setup all available datasets"
    echo ""
    echo "See benchmarks/SETUP.md for download links and full instructions."
    exit 1
}

if [[ $# -lt 1 ]]; then
    usage
fi

case "$1" in
    bigclonebench)
        setup_bigclonebench
        ;;
    gptclonebench)
        setup_gptclonebench
        ;;
    poj104)
        setup_poj104
        ;;
    all)
        setup_bigclonebench
        setup_gptclonebench
        setup_poj104
        ;;
    *)
        err "Unknown dataset: $1"
        usage
        ;;
esac
