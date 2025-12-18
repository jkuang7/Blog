#!/usr/bin/env bash

set -euo pipefail

MAX_SEGMENTS=250000
MAX_CHARS_PER_FILE=95000  # Character limit per file (under 100k with metadata)
ALGO="xz"
FOLDER_MODE=false
USE_FOLDER_OUTPUT=false

R='\033[0;31m'
G='\033[0;32m'
Y='\033[1;33m'
N='\033[0m'

err() { echo -e "${R}ERR: $1${N}" >&2; }
ok() { echo -e "${G}OK: $1${N}"; }
info() { echo -e "${Y}$1${N}"; }

# Aliases for consistent naming
print_error() { err "$@"; }
print_success() { ok "$@"; }
print_info() { info "$@"; }

# Check if file has archive signature
has_sig() {
    local f="$1"
    [[ -f "$f" ]] || return 1
    local h=$(head -n 1 "$f" 2>/dev/null)
    [[ "$h" =~ ^#\ META: ]]
}

# Check if file has pack metadata
has_pack_metadata() {
    local f="$1"
    [[ -f "$f" ]] || return 1
    local h=$(head -n 1 "$f" 2>/dev/null)
    [[ "$h" =~ ^#\ PACK_META: ]]
}

# Get pack metadata from file
get_pack_metadata() {
    local f="$1"
    local h=$(head -n 1 "$f" 2>/dev/null)
    if [[ "$h" =~ ^#\ PACK_META: ]]; then
        echo "$h"
    fi
}

# Extract file signature
get_sig() {
    local f="$1"
    local h=$(head -n 1 "$f" 2>/dev/null)
    if [[ "$h" =~ ^#\ META: ]]; then
        echo "$h"
    fi
}

# Scan directory for data files and group by collection
scan_collections() {
    local d="$1"
    local colls=""
    local found=""

    # Scan text files
    for f in "$d"/*.txt; do
        [[ -f "$f" ]] || continue

        local sig=$(get_sig "$f")
        [[ -z "$sig" ]] && continue

        # Parse signature: # META:name:seg/total:...
        IFS=':' read -r _ cname sinfo _ _ _ <<< "$sig"

        # Check if collection already found
        if [[ ! "$found" =~ "$cname" ]]; then
            found="$found $cname"
            # Count segments for this collection
            local cnt=0
            for x in "$d"/*.txt; do
                local s=$(get_sig "$x")
                if [[ -n "$s" ]]; then
                    IFS=':' read -r _ n _ _ _ _ <<< "$s"
                    [[ "$n" == "$cname" ]] && ((cnt++))
                fi
            done
            echo "$cname:$cnt"
        fi
    done
}

# Find all segments of a specific collection
find_segments() {
    local d="$1"
    local target="$2"
    local segs=()

    for f in "$d"/*.txt "$d"/.*.txt; do
        [[ -f "$f" ]] || continue

        local sig=$(get_sig "$f")
        [[ -z "$sig" ]] && continue

        # Parse signature
        IFS=':' read -r _ cname sinfo _ _ _ <<< "$sig"

        if [[ "$cname" == "$target" ]]; then
            # Extract segment number
            local snum="${sinfo%/*}"
            segs[$snum]="$f"
        fi
    done

    # Return sorted segment files
    for i in "${!segs[@]}"; do
        echo "${segs[$i]}"
    done
}

# Scan directory for packed archives
scan_for_archives() {
    local dir="$1"
    local archives=()
    local found_names=""
    
    for file in "$dir"/*.txt "$dir"/.*.txt; do
        [[ -f "$file" ]] || continue
        
        local metadata=$(get_pack_metadata "$file")
        [[ -z "$metadata" ]] && continue
        
        # Parse metadata: # PACK_META:name:part/total:...
        IFS=':' read -r _ archive_name part_info _ _ _ <<< "$metadata"
        
        # Check if we've already found this archive
        if [[ ! "$found_names" =~ "$archive_name" ]]; then
            found_names="$found_names $archive_name"
            # Find all parts for this archive
            local parts=$(find_archive_parts "$dir" "$archive_name")
            local num_parts=$(echo "$parts" | wc -w)
            echo "${archive_name}:${parts// /,}"
        fi
    done
}

# Find all parts of a specific archive
find_archive_parts() {
    local dir="$1"
    local archive_name="$2"
    local parts=()
    
    for file in "$dir"/*.txt "$dir"/.*.txt; do
        [[ -f "$file" ]] || continue
        
        local metadata=$(get_pack_metadata "$file")
        [[ -z "$metadata" ]] && continue
        
        # Parse metadata
        IFS=':' read -r _ name part_info _ _ _ <<< "$metadata"
        
        if [[ "$name" == "$archive_name" ]]; then
            echo "$file"
        fi
    done
}

pack() {
    local input_dir="$1"
    local output_prefix="${2:-$(basename "$input_dir")}"
    local max_chars="${3:-$MAX_CHARS_PER_FILE}"

    if [[ ! -d "$input_dir" ]]; then
        print_error "Directory not found: $input_dir"
        return 1
    fi

    local output_base="${output_prefix}_xz"

    if [[ "$USE_FOLDER_OUTPUT" == "true" ]]; then
        local output_dir="$output_base"

        if [[ -d "$output_dir" ]]; then
            print_info "Output directory $output_dir already exists"
            echo -n "Overwrite? (Enter=yes, Ctrl+C=cancel): "
            read -r response
            if [[ -n "$response" && ! "$response" =~ ^[Yy]$ ]]; then
                print_info "Packing cancelled"
                return 1
            fi
            rm -rf "$output_dir"
        fi

        mkdir -p "$output_dir"
        print_info "Packing $input_dir -> $output_dir/"
    else
        if ls "${output_base}_part_"*.txt >/dev/null 2>&1; then
            print_info "Output files ${output_base}_part_*.txt already exist"
            echo -n "Overwrite? (Enter=yes, Ctrl+C=cancel): "
            read -r response
            if [[ -n "$response" && ! "$response" =~ ^[Yy]$ ]]; then
                print_info "Packing cancelled"
                return 1
            fi
            rm -f "${output_base}_part_"*.txt
        fi
        print_info "Packing $input_dir -> ${output_base}_part_*.txt"
    fi

    local tar_file="/tmp/pack_$$.tar"
    local packignore_file="$input_dir/.packignore"

    if [[ -f "$packignore_file" ]]; then
        print_info "Found .packignore file, excluding specified patterns"

        local exclude_file="/tmp/pack_exclude_$$"
        while IFS= read -r pattern || [[ -n "$pattern" ]]; do
            [[ -z "$pattern" || "$pattern" =~ ^[[:space:]]*# ]] && continue
            echo "$(basename "$input_dir")/$pattern" >> "$exclude_file"
        done < "$packignore_file"

        tar -cf "$tar_file" -C "$(dirname "$input_dir")" \
            --exclude-from="$exclude_file" \
            "$(basename "$input_dir")" 2>/dev/null
        rm -f "$exclude_file"
    else
        tar -cf "$tar_file" -C "$(dirname "$input_dir")" "$(basename "$input_dir")" 2>/dev/null
    fi

    local compressed_file="/tmp/pack_$$.tar.xz"
    xz -9 -c "$tar_file" > "$compressed_file"
    rm -f "$tar_file"

    local encoded_file="/tmp/pack_$$.b64"
    # Use fold to wrap base64 output at 76 characters per line for better handling
    base64 < "$compressed_file" | fold -w 76 > "$encoded_file"
    rm -f "$compressed_file"

    # Calculate parts based on character count
    local total_chars=$(wc -c < "$encoded_file")
    local num_parts=$(( (total_chars + max_chars - 1) / max_chars ))

    # Split by byte count instead of line count
    split -b "$max_chars" "$encoded_file" "/tmp/pack_part_$$_"

    local part_num=1
    for part_file in /tmp/pack_part_$$_*; do
        local output_file
        if [[ "$USE_FOLDER_OUTPUT" == "true" ]]; then
            output_file="${output_dir}/part_${part_num}.txt"
        else
            output_file="${output_base}_part_${part_num}.txt"
        fi

        # Each part gets its own metadata with part number
        local metadata="# PACK_META:$(basename "$input_dir"):${part_num}/${num_parts}:$total_chars:$(date -u +%s):xz"
        {
            echo "$metadata"
            cat "$part_file"
        } > "$output_file"
        rm -f "$part_file"
        ((part_num++))
    done

    rm -f "$encoded_file"

    if [[ "$USE_FOLDER_OUTPUT" == "true" ]]; then
        print_success "Created $num_parts part(s) in $output_dir/"
        echo "Total size: $(du -sh "$output_dir" | cut -f1)"
    else
        print_success "Created $num_parts part file(s): ${output_base}_part_*.txt"
        local total_size=$(du -ch "${output_base}_part_"*.txt 2>/dev/null | tail -1 | cut -f1)
        echo "Total size: $total_size"
    fi
}

unpack_from_parts() {
    local part_files=("$@")
    local output_dir="."

    # Check if last argument is a directory path (not a file)
    local last_arg="${@: -1}"
    if [[ ! -f "$last_arg" && "$#" -gt 1 ]]; then
        output_dir="$last_arg"
        part_files=("${@:1:$#-1}")
    fi

    if [[ ${#part_files[@]} -eq 0 ]]; then
        print_error "No part files provided"
        return 1
    fi

    print_info "Found ${#part_files[@]} part(s) to unpack"

    # Sort part files by part number from metadata
    local sorted_parts=()
    local archive_name=""
    local total_parts=0

    for file in "${part_files[@]}"; do
        local metadata=$(get_pack_metadata "$file")
        if [[ -z "$metadata" ]]; then
            print_error "No metadata in file: $file"
            return 1
        fi

        # Parse metadata: # PACK_META:name:part/total:...
        IFS=':' read -r _ name part_info _ _ _ <<< "$metadata"
        local part_num="${part_info%/*}"
        local num_parts="${part_info#*/}"

        # Verify all parts are from same archive
        if [[ -z "$archive_name" ]]; then
            archive_name="$name"
            total_parts="$num_parts"
        elif [[ "$archive_name" != "$name" ]]; then
            print_error "Mixed archives: $archive_name and $name"
            return 1
        fi

        sorted_parts[$part_num]="$file"
    done

    # Verify we have all parts
    if [[ ${#sorted_parts[@]} -ne $total_parts ]]; then
        print_error "Missing parts: have ${#sorted_parts[@]} of $total_parts"
        return 1
    fi

    local orig_name="$archive_name"

    local target_path="$output_dir/$orig_name"
    if [[ -e "$target_path" ]]; then
        print_info "Target already exists: $target_path"
        echo -n "Replace? (Enter=yes, Ctrl+C=cancel): "
        read -r response

        if [[ -z "$response" || "$response" =~ ^[Yy]$ ]]; then
            print_info "Replacing $target_path..."
            rm -rf "$target_path"
        else
            print_info "Unpacking cancelled"
            return 1
        fi
    fi

    local combined_file="/tmp/pack_combined_$$.b64"

    # Combine all parts in order, skipping metadata lines
    {
        for i in $(seq 1 $total_parts); do
            if [[ -n "${sorted_parts[$i]}" ]]; then
                tail -n +2 "${sorted_parts[$i]}"
            else
                print_error "Missing part $i"
                return 1
            fi
        done
    } > "$combined_file"

    local compressed_file="/tmp/pack_$$.tar.xz"
    base64 -d < "$combined_file" > "$compressed_file"
    rm -f "$combined_file"

    cd "$output_dir"
    xz -dc "$compressed_file" | tar -xf -
    rm -f "$compressed_file"

    # Clean up flat files if they're not in a dedicated directory
    local should_clean=true
    for file in "${sorted_parts[@]}"; do
        if [[ "$(dirname "$file")" == *"_xz" ]]; then
            should_clean=false
            break
        fi
    done

    if [[ "$should_clean" == "true" ]]; then
        for file in "${sorted_parts[@]}"; do
            rm -f "$file"
        done
        print_info "Cleaned up packed files"
    fi

    print_success "Unpacked to: $output_dir/$orig_name"
}

main() {
    if [[ $# -eq 0 ]]; then
        echo "Usage:"
        echo "  pack <path>     - Smart pack/unpack based on content"
        echo ""
        echo "Examples:"
        echo "  pack /Users/jian/Dev/IGNORE     # Pack directory"
        echo "  pack /Users/jian/Dev/            # Unpack if metadata found"
        echo "  pack /path/to/any_file.txt      # Unpack if has metadata"
        return 1
    fi

    local target="$1"
    shift

    # Handle single file
    if [[ -f "$target" ]]; then
        if has_pack_metadata "$target"; then
            print_info "Detected packed file with metadata"
            # Extract archive name and find all parts
            local metadata=$(get_pack_metadata "$target")
            IFS=':' read -r _ archive_name part_info _ _ _ <<< "$metadata"

            local dir=$(dirname "$target")
            print_info "Looking for all parts of '$archive_name' in $dir"

            local part_files=($(find_archive_parts "$dir" "$archive_name"))
            if [[ ${#part_files[@]} -eq 0 ]]; then
                print_error "Could not find all parts for archive: $archive_name"
                return 1
            fi

            unpack_from_parts "${part_files[@]}" "$@"
        else
            print_error "File has no pack metadata: $target"
            return 1
        fi

    # Handle directory
    elif [[ -d "$target" ]]; then
        # Scan for archives in directory
        local archives=($(scan_for_archives "$target"))

        if [[ ${#archives[@]} -gt 0 ]]; then
            # Found packed archives
            if [[ ${#archives[@]} -eq 1 ]]; then
                # Single archive - unpack it
                local archive_info="${archives[0]}"
                local archive_name="${archive_info%%:*}"
                print_info "Found packed archive: $archive_name"

                local part_files=($(find_archive_parts "$target" "$archive_name"))
                unpack_from_parts "${part_files[@]}" "$@"
            else
                # Multiple archives - let user choose
                print_info "Found ${#archives[@]} packed archives:"
                local i=1
                for archive_info in "${archives[@]}"; do
                    local archive_name="${archive_info%%:*}"
                    local files="${archive_info#*:}"
                    local num_files=$(echo "$files" | tr ',' '\n' | wc -l)
                    echo "  $i. $archive_name ($num_files parts)"
                    ((i++))
                done
                echo "  $i. Unpack all"

                echo -n "Choose (1-$i): "
                read -r choice

                if [[ "$choice" -eq "$i" ]]; then
                    # Unpack all
                    for archive_info in "${archives[@]}"; do
                        local archive_name="${archive_info%%:*}"
                        print_info "Unpacking $archive_name..."
                        local part_files=($(find_archive_parts "$target" "$archive_name"))
                        unpack_from_parts "${part_files[@]}" "$@"
                    done
                elif [[ "$choice" -ge 1 && "$choice" -le ${#archives[@]} ]]; then
                    # Unpack selected
                    local archive_info="${archives[$((choice-1))]}"
                    local archive_name="${archive_info%%:*}"
                    local part_files=($(find_archive_parts "$target" "$archive_name"))
                    unpack_from_parts "${part_files[@]}" "$@"
                else
                    print_error "Invalid choice"
                    return 1
                fi
            fi
        else
            # No packed files found - pack the directory
            print_info "No packed files found, packing directory..."
            pack "$target" "$@"
        fi
    else
        print_error "Path not found: $target"
        return 1
    fi
}

main "$@"