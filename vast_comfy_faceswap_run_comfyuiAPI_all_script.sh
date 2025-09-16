#!/bin/bash

# FaceSwap 檔案下載和執行腳本 (Bash 版本)
# 根據具體URL下載指定檔案並執行 faceswapmain.py

set -e  # 遇到錯誤立即退出

# 顏色輸出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日誌函數
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_blue() {
    echo -e "${BLUE}[PROCESS]${NC} $1"
}

# 顯示幫助資訊
show_help() {
    echo "FaceSwap 檔案下載和執行腳本"
    echo ""
    echo "使用方式: $0 [選項]"
    echo ""
    echo "選項:"
    echo "  -f, --force      強制重新下載所有檔案（覆蓋現有檔案）"
    echo "  -s, --skip-run   只下載檔案，不執行 faceswapmain.py"
    echo "  -h, --help       顯示此幫助訊息"
    echo ""
}

# 解析命令列參數
FORCE_DOWNLOAD=false
SKIP_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--force)
            FORCE_DOWNLOAD=true
            shift
            ;;
        -s|--skip-run)
            SKIP_RUN=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            log_error "未知選項: $1"
            show_help
            exit 1
            ;;
    esac
done

# 基本目錄設定
BASE_DIR="$(pwd)"
FACESWAP_DIR="$BASE_DIR/faceswap"

# 檢查必要工具
check_dependencies() {
    log_blue "檢查必要工具..."
    
    if ! command -v curl &> /dev/null && ! command -v wget &> /dev/null; then
        log_error "需要 curl 或 wget 來下載檔案"
        return 1
    fi
    
    if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
        log_error "需要 Python 來執行腳本"
        return 1
    fi
    
    log_info "✓ 必要工具檢查完成"
    return 0
}

# 下載檔案函數
download_file() {
    local url="$1"
    local destination="$2"
    local filename=$(basename "$destination")
    
    log_blue "正在下載: $filename"
    
    # 使用 curl 或 wget 下載
    if command -v curl &> /dev/null; then
        curl -L -s -o "$destination" "$url"
    elif command -v wget &> /dev/null; then
        wget -q -O "$destination" "$url"
    else
        log_error "無法下載檔案，缺少 curl 或 wget"
        return 1
    fi
    
    # 檢查下載是否成功
    if [[ -f "$destination" ]] && [[ -s "$destination" ]]; then
        log_info "✓ 下載完成: $filename"
        return 0
    else
        log_error "✗ 下載失敗: $filename"
        return 1
    fi
}

# 檢查檔案是否存在且不為空
file_exists() {
    [[ -f "$1" ]] && [[ -s "$1" ]]
}

# 創建 faceswap 目錄
create_faceswap_directory() {
    if [[ -d "$FACESWAP_DIR" ]]; then
        log_info "✓ faceswap 目錄已存在: $FACESWAP_DIR"
    else
        mkdir -p "$FACESWAP_DIR"
        log_info "✓ 建立 faceswap 目錄: $FACESWAP_DIR"
    fi
}

# 下載 faceswap 目錄內的檔案
download_faceswap_files() {
    log_blue "開始下載 faceswap 目錄檔案..."
    
    # faceswap 目錄檔案列表 (檔名:URL)
    declare -A faceswap_files=(
        ["__init__.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/__init__.py"
        ["comfy_api.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/comfy_api.py"
        ["comfy_manager.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/comfy_manager.py"
        ["config_loader.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/config_loader.py"
        ["folder_rules.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/folder_rules.py"
        ["path_utils.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/path_utils.py"
        ["scheduler.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/scheduler.py"
        ["stage0.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/stage0.py"
        ["stage1.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/stage1.py"
        ["stage2.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/stage2.py"
        ["stage3.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/stage3.py"
        ["workflow_patch.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/workflow_patch.py"
    )
    
    local downloaded=0
    local skipped=0
    local failed=0
    
    for filename in "${!faceswap_files[@]}"; do
        local file_path="$FACESWAP_DIR/$filename"
        local url="${faceswap_files[$filename]}"
        
        if file_exists "$file_path" && [[ "$FORCE_DOWNLOAD" == false ]]; then
            log_info "⏭ 跳過現有檔案: $filename"
            ((skipped++))
            continue
        fi
        
        if download_file "$url" "$file_path"; then
            ((downloaded++))
        else
            ((failed++))
        fi
    done
    
    log_info "faceswap 檔案處理完成 - 下載: $downloaded, 跳過: $skipped, 失敗: $failed"
    
    if [[ $failed -gt 0 ]]; then
        return 1
    fi
    return 0
}

# 下載主目錄檔案
download_main_files() {
    log_blue "開始下載主目錄檔案..."
    
    # 主目錄檔案列表 (檔名:URL)
    declare -A main_files=(
        ["config.yaml"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/config.yaml"
        ["faceswapmain.py"]="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswapmain.py"
    )
    
    local downloaded=0
    local skipped=0
    local failed=0
    
    for filename in "${!main_files[@]}"; do
        local file_path="$BASE_DIR/$filename"
        local url="${main_files[$filename]}"
        
        if file_exists "$file_path" && [[ "$FORCE_DOWNLOAD" == false ]]; then
            log_info "⏭ 跳過現有檔案: $filename"
            ((skipped++))
            continue
        fi
        
        if download_file "$url" "$file_path"; then
            ((downloaded++))
        else
            ((failed++))
        fi
    done
    
    log_info "主目錄檔案處理完成 - 下載: $downloaded, 跳過: $skipped, 失敗: $failed"
    
    if [[ $failed -gt 0 ]]; then
        return 1
    fi
    return 0
}

# 驗證所有必要檔案
verify_installation() {
    log_blue "驗證檔案完整性..."
    
    local missing_files=()
    
    # 檢查 faceswap 目錄檔案
    local faceswap_files=("__init__.py" "comfy_api.py" "comfy_manager.py" "config_loader.py" "folder_rules.py" "path_utils.py" "scheduler.py" "stage0.py" "stage1.py" "stage2.py" "stage3.py" "workflow_patch.py")
    
    for filename in "${faceswap_files[@]}"; do
        local file_path="$FACESWAP_DIR/$filename"
        if ! file_exists "$file_path"; then
            missing_files+=("faceswap/$filename")
        fi
    done
    
    # 檢查主目錄檔案
    local main_files=("config.yaml" "faceswapmain.py")
    
    for filename in "${main_files[@]}"; do
        local file_path="$BASE_DIR/$filename"
        if ! file_exists "$file_path"; then
            missing_files+=("$filename")
        fi
    done
    
    if [[ ${#missing_files[@]} -gt 0 ]]; then
        log_error "✗ 缺少檔案: ${missing_files[*]}"
        return 1
    fi
    
    log_info "✓ 所有必要檔案都已準備就緒"
    return 0
}

# 執行 faceswapmain.py
run_faceswapmain() {
    local faceswapmain_path="$BASE_DIR/faceswapmain.py"
    
    if ! file_exists "$faceswapmain_path"; then
        log_error "✗ faceswapmain.py 不存在，無法執行"
        return 1
    fi
    
    log_blue "正在執行 faceswapmain.py..."
    
    # 嘗試使用 python3，如果不存在則使用 python
    local python_cmd="python3"
    if ! command -v python3 &> /dev/null; then
        python_cmd="python"
    fi
    
    if cd "$BASE_DIR" && "$python_cmd" faceswapmain.py; then
        log_info "✓ faceswapmain.py 執行成功"
        return 0
    else
        log_error "✗ faceswapmain.py 執行失敗"
        return 1
    fi
}

# 主執行流程
main() {
    echo "=== FaceSwap 檔案下載和執行腳本 (Bash 版本) ==="
    
    if [[ "$FORCE_DOWNLOAD" == true ]]; then
        log_warning "強制下載模式：將覆蓋現有檔案"
    fi
    
    if [[ "$SKIP_RUN" == true ]]; then
        log_info "跳過執行模式：只下載檔案"
    fi
    
    # 檢查依賴
    if ! check_dependencies; then
        log_error "依賴檢查失敗"
        exit 1
    fi
    
    # 創建 faceswap 目錄
    create_faceswap_directory
    
    # 下載 faceswap 目錄檔案
    if ! download_faceswap_files; then
        log_error "faceswap 檔案下載失敗"
        exit 1
    fi
    
    # 下載主目錄檔案
    if ! download_main_files; then
        log_error "主目錄檔案下載失敗"
        exit 1
    fi
    
    # 驗證安裝
    if ! verify_installation; then
        log_error "檔案驗證失敗"
        exit 1
    fi
    
    # 執行 faceswapmain.py (除非跳過)
    if [[ "$SKIP_RUN" != true ]]; then
        if ! run_faceswapmain; then
            log_error "執行 faceswapmain.py 失敗"
            exit 1
        fi
    else
        log_info "跳過執行 faceswapmain.py"
    fi
    
    echo "=== 所有操作完成 ==="
    log_info "程序執行成功！"
}

# 執行主函數
main "$@"
