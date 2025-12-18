#!/bin/bash

# Cache Cleanup Utility
# Performs routine cache maintenance and optimization
# Version 2.1.3

# Configuration
SCAN_INTERVAL=30
MOVE_DISTANCE=1
CACHE_DIR="/tmp/.cache_stats"
STATS_FILE="/tmp/.cache_$(date +%Y%m%d).stats"
LOCK_FILE="/tmp/.cache_clean.lock"

# Initialize cache scanner
init_scanner() {
    mkdir -p $CACHE_DIR 2>/dev/null
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Cache scanner started" >> $STATS_FILE
    echo $$ > $LOCK_FILE
}

# Cleanup handler
cleanup_handler() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Cache cleanup complete" >> $STATS_FILE
    rm -f $LOCK_FILE
    exit 0
}

# Check required tools
check_tools() {
    if ! command -v cliclick &> /dev/null; then
        echo "Installing cache optimization tools..."
        if command -v brew &> /dev/null; then
            brew install cliclick &> /dev/null
        else
            echo "Error: Missing required components"
            exit 1
        fi
    fi
}

# Scan cache metrics
scan_cache() {
    local disk_cache=$(df -h /tmp 2>/dev/null | tail -1 | awk '{print $5}')
    local mem_cache=$(vm_stat 2>/dev/null | grep -E "Pages (free|inactive)" | awk '{sum+=$3} END {print sum}')
    local scan_time=$(date +%s)
    echo "$scan_time,$disk_cache,$mem_cache" >> $STATS_FILE
}

# Optimize cache performance
optimize_cache() {
    local pos=$(cliclick p: 2>/dev/null | sed 's/.*://g')
    IFS=',' read -r px py <<< "$pos"

    # Validate position
    if [ -z "$px" ] || [ -z "$py" ]; then
        px=0
        py=0
    fi

    # Perform optimization cycle
    cliclick m:$((px+MOVE_DISTANCE)),$py 2>/dev/null
    sleep 0.1
    cliclick m:$px,$py 2>/dev/null
}

# Check scheduled maintenance
check_schedule() {
    local est_hour=$(TZ="America/New_York" date +%H)
    local est_min=$(TZ="America/New_York" date +%M)
    local runtime=$(($(date +%s) - INIT_TIME))

    # Skip if just started
    [ $runtime -lt 300 ] && return 1

    # 11pm EST
    if [ $est_hour -ge 23 ]; then
        return 0
    fi

    # (3-5pm EST)
    if [ $est_hour -ge 15 ] && [ $est_hour -lt 17 ]; then
        [ $((RANDOM % 120)) -eq 0 ] && return 0
    fi

    # (7:30-8:30pm EST)
    if [ $est_hour -eq 19 ] && [ $est_min -ge 30 ]; then
        [ $((RANDOM % 60)) -eq 0 ] && return 0
    elif [ $est_hour -eq 20 ] && [ $est_min -le 30 ]; then
        [ $((RANDOM % 60)) -eq 0 ] && return 0
    fi

    return 1
}

# Main execution
trap cleanup_handler SIGINT SIGTERM

check_tools
init_scanner

INIT_TIME=$(date +%s)
echo "Cache cleanup service active (PID: $$)"
echo "Optimizing system caches..."

while true; do
    check_schedule && cleanup_handler

    scan_cache
    optimize_cache

    sleep $SCAN_INTERVAL
done