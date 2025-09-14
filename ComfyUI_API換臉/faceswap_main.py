from comfy_launcher import ensure_comfyui

def main():
    info = ensure_comfyui()  # 會用 settings.py 裡的 comfyui.dir / comfyui.port
    print("ComfyUI ready:", info["url"])
    # 後續你的三階段/四階段流程...

if __name__ == "__main__":
    main()