#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FaceSwap 精確檔案下載和執行腳本
根據具體URL下載指定檔案並執行 faceswapmain.py
"""

import os
import sys
import subprocess
import requests
import logging
import time
from pathlib import Path

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('faceswap_download.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class FaceSwapDownloader:
    def __init__(self):
        self.base_dir = Path.cwd()
        self.faceswap_dir = self.base_dir / "faceswap"
        
        # faceswap 目錄內的檔案 URL (轉換為 raw 格式)
        self.faceswap_files = {
            "__init__.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/__init__.py",
            "comfy_api.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/comfy_api.py",
            "comfy_manager.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/comfy_manager.py",
            "config_loader.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/config_loader.py",
            "folder_rules.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/folder_rules.py",
            "path_utils.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/path_utils.py",
            "scheduler.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/scheduler.py",
            "stage0.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/stage0.py",
            "stage1.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/stage1.py",
            "stage2.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/stage2.py",
            "stage3.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/stage3.py",
            "workflow_patch.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswap/workflow_patch.py"
        }
        
        # 主目錄的檔案 URL (轉換為 raw 格式)
        self.main_files = {
            "config.yaml": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/config.yaml",
            "faceswapmain.py": "https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/faceswap_all/faceswapmain.py"
        }

    def check_dependencies(self):
        """檢查必要的依賴套件"""
        logger.info("檢查必要套件...")
        try:
            import requests
            logger.info("✓ requests 可用")
            return True
        except ImportError:
            logger.info("正在安裝 requests...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
                logger.info("✓ requests 安裝完成")
                return True
            except subprocess.CalledProcessError:
                logger.error("✗ 無法安裝 requests")
                return False

    def create_faceswap_directory(self):
        """檢查並創建 faceswap 目錄"""
        if self.faceswap_dir.exists():
            logger.info(f"✓ faceswap 目錄已存在: {self.faceswap_dir}")
        else:
            self.faceswap_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"✓ 建立 faceswap 目錄: {self.faceswap_dir}")

    def download_file(self, url, destination):
        """下載單個檔案"""
        try:
            logger.info(f"正在下載: {destination.name}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            with open(destination, 'w', encoding='utf-8') as f:
                f.write(response.text)
            
            logger.info(f"✓ 下載完成: {destination.name}")
            return True
        except Exception as e:
            logger.error(f"✗ 下載失敗 {destination.name}: {e}")
            return False

    def check_file_exists(self, file_path):
        """檢查檔案是否存在"""
        return file_path.exists() and file_path.is_file()

    def download_faceswap_files(self, force_download=False):
        """下載 faceswap 目錄內的檔案"""
        logger.info("開始下載 faceswap 目錄檔案...")
        
        downloaded_count = 0
        skipped_count = 0
        failed_count = 0
        
        for filename, url in self.faceswap_files.items():
            file_path = self.faceswap_dir / filename
            
            if self.check_file_exists(file_path) and not force_download:
                logger.info(f"⏭ 跳過現有檔案: {filename}")
                skipped_count += 1
                continue
            
            if self.download_file(url, file_path):
                downloaded_count += 1
            else:
                failed_count += 1
        
        logger.info(f"faceswap 檔案處理完成 - 下載: {downloaded_count}, 跳過: {skipped_count}, 失敗: {failed_count}")
        return failed_count == 0

    def download_main_files(self, force_download=False):
        """下載主目錄檔案"""
        logger.info("開始下載主目錄檔案...")
        
        downloaded_count = 0
        skipped_count = 0
        failed_count = 0
        
        for filename, url in self.main_files.items():
            file_path = self.base_dir / filename
            
            if self.check_file_exists(file_path) and not force_download:
                logger.info(f"⏭ 跳過現有檔案: {filename}")
                skipped_count += 1
                continue
            
            if self.download_file(url, file_path):
                downloaded_count += 1
            else:
                failed_count += 1
        
        logger.info(f"主目錄檔案處理完成 - 下載: {downloaded_count}, 跳過: {skipped_count}, 失敗: {failed_count}")
        return failed_count == 0

    def verify_installation(self):
        """驗證所有必要檔案是否存在"""
        logger.info("驗證檔案完整性...")
        
        missing_files = []
        
        # 檢查 faceswap 目錄檔案
        for filename in self.faceswap_files.keys():
            file_path = self.faceswap_dir / filename
            if not self.check_file_exists(file_path):
                missing_files.append(f"faceswap/{filename}")
        
        # 檢查主目錄檔案
        for filename in self.main_files.keys():
            file_path = self.base_dir / filename
            if not self.check_file_exists(file_path):
                missing_files.append(filename)
        
        if missing_files:
            logger.error(f"✗ 缺少檔案: {missing_files}")
            return False
        
        logger.info("✓ 所有必要檔案都已準備就緒")
        return True

    def run_faceswapmain(self):
        """執行 faceswapmain.py"""
        faceswapmain_path = self.base_dir / "faceswapmain.py"
        
        if not self.check_file_exists(faceswapmain_path):
            logger.error("✗ faceswapmain.py 不存在，無法執行")
            return False
        
        logger.info("正在執行 faceswapmain.py...")
        
        try:
            # 在當前目錄執行
            result = subprocess.run([
                sys.executable, "faceswapmain.py"
            ], cwd=self.base_dir, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info("✓ faceswapmain.py 執行成功")
                if result.stdout:
                    logger.info(f"輸出:\n{result.stdout}")
                return True
            else:
                logger.error(f"✗ faceswapmain.py 執行失敗，返回碼: {result.returncode}")
                if result.stderr:
                    logger.error(f"錯誤:\n{result.stderr}")
                if result.stdout:
                    logger.error(f"輸出:\n{result.stdout}")
                return False
                
        except Exception as e:
            logger.error(f"✗ 執行 faceswapmain.py 時發生錯誤: {e}")
            return False

    def run(self, force_download=False, skip_run=False):
        """主執行流程"""
        logger.info("=== FaceSwap 檔案下載和執行腳本 ===")
        
        # 檢查依賴
        if not self.check_dependencies():
            logger.error("依賴檢查失敗")
            return False
        
        # 創建 faceswap 目錄
        self.create_faceswap_directory()
        
        # 下載 faceswap 目錄檔案
        if not self.download_faceswap_files(force_download):
            logger.error("faceswap 檔案下載失敗")
            return False
        
        # 下載主目錄檔案
        if not self.download_main_files(force_download):
            logger.error("主目錄檔案下載失敗")
            return False
        
        # 驗證安裝
        if not self.verify_installation():
            logger.error("檔案驗證失敗")
            return False
        
        # 執行 faceswapmain.py (除非跳過)
        if not skip_run:
            if not self.run_faceswapmain():
                logger.error("執行 faceswapmain.py 失敗")
                return False
        else:
            logger.info("跳過執行 faceswapmain.py")
        
        logger.info("=== 所有操作完成 ===")
        return True


def main():
    """主函數"""
    import argparse
    
    parser = argparse.ArgumentParser(description='FaceSwap 檔案下載和執行腳本')
    parser.add_argument('--force', '-f', action='store_true', 
                       help='強制重新下載所有檔案（覆蓋現有檔案）')
    parser.add_argument('--skip-run', '-s', action='store_true',
                       help='只下載檔案，不執行 faceswapmain.py')
    parser.add_argument('--version', '-v', action='version', version='FaceSwap Downloader 1.0')
    
    args = parser.parse_args()
    
    downloader = FaceSwapDownloader()
    
    try:
        success = downloader.run(force_download=args.force, skip_run=args.skip_run)
        if success:
            logger.info("程序執行成功！")
        else:
            logger.error("程序執行失敗！")
            return False
            
    except KeyboardInterrupt:
        logger.info("用戶中斷執行")
        return False
    except Exception as e:
        logger.error(f"執行過程中發生未預期的錯誤: {e}")
        return False
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)