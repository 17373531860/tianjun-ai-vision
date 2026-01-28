# -*- coding: utf-8 -*-
"""
Tianjun AI Vision System - Startup Fix Patch v4
"""

import os
import sys
import json
import shutil


def read_asar_header(asar_path):
    with open(asar_path, 'rb') as f:
        data = f.read(1024 * 1024)
        start_marker = b'{"files":'
        start_pos = data.find(start_marker)
        if start_pos == -1:
            raise ValueError("Cannot find JSON header in asar file")
        brace_count = 0
        end_pos = start_pos
        for i in range(start_pos, len(data)):
            if data[i:i+1] == b'{':
                brace_count += 1
            elif data[i:i+1] == b'}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i + 1
                    break
        json_data = data[start_pos:end_pos]
        header = json.loads(json_data.decode('utf-8'))
        content_offset = end_pos
        if content_offset % 4 != 0:
            content_offset += 4 - (content_offset % 4)
        return header, content_offset


def extract_file_from_asar(asar_path, file_info, content_offset):
    with open(asar_path, 'rb') as f:
        offset = content_offset + int(file_info['offset'])
        size = int(file_info['size'])
        f.seek(offset)
        return f.read(size)


def extract_asar(asar_path, output_dir):
    header, content_offset = read_asar_header(asar_path)
    def extract_recursive(files, current_path):
        for name, info in files.items():
            file_path = os.path.join(current_path, name)
            if 'files' in info:
                os.makedirs(file_path, exist_ok=True)
                extract_recursive(info['files'], file_path)
            elif 'offset' in info:
                os.makedirs(os.path.dirname(file_path) if os.path.dirname(file_path) else current_path, exist_ok=True)
                content = extract_file_from_asar(asar_path, info, content_offset)
                with open(file_path, 'wb') as f:
                    f.write(content)
    os.makedirs(output_dir, exist_ok=True)
    extract_recursive(header.get('files', {}), output_dir)
    return header


def apply_patch(resources_dir):
    asar_path = os.path.join(resources_dir, 'app.asar')
    backup_path = os.path.join(resources_dir, 'app.asar.original')
    app_dir = os.path.join(resources_dir, 'app')
    
    print("[INFO] ASAR file: {}".format(asar_path))
    print("[INFO] App folder: {}".format(app_dir))
    
    if not os.path.exists(asar_path) and not os.path.exists(app_dir):
        print("[ERROR] Neither app.asar nor app folder found")
        return False
    
    try:
        if os.path.exists(asar_path):
            if not os.path.exists(backup_path):
                print("[STEP] Backing up app.asar...")
                shutil.copy2(asar_path, backup_path)
            
            print("[STEP] Extracting app.asar to app folder...")
            # Keep existing dist folder if it exists
            dist_backup = None
            dist_path = os.path.join(app_dir, 'dist')
            if os.path.exists(dist_path):
                dist_backup = os.path.join(resources_dir, '_dist_backup')
                if os.path.exists(dist_backup):
                    shutil.rmtree(dist_backup)
                shutil.move(dist_path, dist_backup)
                print("[INFO] Backed up existing dist folder")
            
            if os.path.exists(app_dir):
                shutil.rmtree(app_dir)
            extract_asar(asar_path, app_dir)
            
            # Restore dist folder
            if dist_backup and os.path.exists(dist_backup):
                shutil.move(dist_backup, dist_path)
                print("[INFO] Restored dist folder")
            
            print("[INFO] Extraction complete")
            
            disabled_path = os.path.join(resources_dir, 'app.asar.disabled')
            if os.path.exists(disabled_path):
                os.remove(disabled_path)
            os.rename(asar_path, disabled_path)
            print("[INFO] app.asar disabled")
        
        # Modify backend-manager.js
        backend_manager_path = os.path.join(app_dir, 'backend-manager.js')
        if os.path.exists(backend_manager_path):
            print("[STEP] Modifying backend-manager.js...")
            with open(backend_manager_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            mods = []
            if "env.PYTHONHOME = pythonDir;" in content and "// env.PYTHONHOME" not in content:
                content = content.replace("env.PYTHONHOME = pythonDir;", "// env.PYTHONHOME = pythonDir;")
                mods.append("Commented out PYTHONHOME")
            for t in ["60000", "120000", "180000"]:
                old = "startupTimeout: options.startupTimeout || {},".format(t)
                if old in content:
                    content = content.replace(old, "startupTimeout: options.startupTimeout || 300000,")
                    mods.append("Increased startup timeout")
                    break
            if "PYTHONNOUSERSITE" not in content and "env.PYTHONDONTWRITEBYTECODE" in content:
                content = content.replace("env.PYTHONDONTWRITEBYTECODE = '1';", 
                    "env.PYTHONDONTWRITEBYTECODE = '1';\n      env.PYTHONNOUSERSITE = '1';")
                mods.append("Added PYTHONNOUSERSITE")
            if "timeout: 2000," in content:
                content = content.replace("timeout: 2000,", "timeout: 10000,")
                mods.append("Increased health check timeout")
            if "setTimeout(resolve, 500)" in content:
                content = content.replace("setTimeout(resolve, 500)", "setTimeout(resolve, 2000)")
                mods.append("Increased health check interval")
            if "hostname: this.options.host," in content:
                content = content.replace("hostname: this.options.host,", "hostname: '127.0.0.1',")
                mods.append("Fixed health check to IPv4")
            
            if mods:
                with open(backend_manager_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print("[INFO] backend-manager.js: {} mods".format(len(mods)))
        
        # Modify main.js - fix file loading
        main_js_path = os.path.join(app_dir, 'main.js')
        if os.path.exists(main_js_path):
            print("[STEP] Modifying main.js...")
            with open(main_js_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            mods = []
            
            # Add webSecurity: false to allow local file loading
            if "webSecurity:" not in content:
                old_prefs = "webPreferences: {\n      nodeIntegration: false,"
                new_prefs = "webPreferences: {\n      webSecurity: false,\n      nodeIntegration: false,"
                if old_prefs in content:
                    content = content.replace(old_prefs, new_prefs)
                    mods.append("Added webSecurity: false")
            
            # Add path logging
            if "console.log('[App] Index path:'" not in content:
                old_load = "mainWindow.loadFile(indexPath);"
                new_load = "console.log('[App] Index path:', indexPath);\n    mainWindow.loadFile(indexPath);"
                if old_load in content:
                    content = content.replace(old_load, new_load)
                    mods.append("Added index path logging")
            
            if mods:
                with open(main_js_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print("[INFO] main.js: {} mods".format(len(mods)))
        
        return True
        
    except Exception as e:
        print("[ERROR] Patch failed: {}".format(e))
        import traceback
        traceback.print_exc()
        return False


def main():
    print("=" * 50)
    print("Tianjun AI Vision System - Patch v4")
    print("=" * 50)
    print()
    
    if len(sys.argv) < 2:
        print("Usage: python patch.py <resources_directory>")
        sys.exit(1)
    
    resources_dir = sys.argv[1]
    if not os.path.isdir(resources_dir):
        print("[ERROR] Directory not found: {}".format(resources_dir))
        sys.exit(1)
    
    success = apply_patch(resources_dir)
    
    if success:
        print()
        print("=" * 50)
        print("Patch v4 applied!")
        print("=" * 50)
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
