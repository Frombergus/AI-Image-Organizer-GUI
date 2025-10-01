import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import shutil
import base64
import io
from PIL import Image
import ollama
from tqdm import tqdm

# Configuration
MODEL_NAME = 'redule26/huihui_ai_qwen2.5-vl-7b-abliterated:latest'
DEFAULT_CATEGORIES = ["birthdays", "vacations", "family_photos", "child_photos", "other", "nudity"]

# Magic bytes for file detection
MAGIC_BYTES = {
    "images": {
        b'\xFF\xD8\xFF': 'jpg',
        b'\x89PNG\r\n\x1A\n': 'png',
        b'GIF8': 'gif',
        b'BM': 'bmp'
    }
}

def get_file_type(file_path):
    """Detect file type based on magic bytes."""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(32)
            for category, signatures in MAGIC_BYTES.items():
                for magic, ext in signatures.items():
                    if header.startswith(magic):
                        return category, ext
    except Exception as e:
        print(f"[ERROR] Failed reading {file_path}: {e}")
    return None, None

def is_valid_image_size(image_path, min_size):
    """Check if image dimensions meet minimum size requirement."""
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            return width >= min_size and height >= min_size
    except Exception as e:
        print(f"[WARNING] Could not load image for size check {image_path}: {e}")
        return False

def extract_images(source_path, output_base, min_size):
    """Extract images from source directory to output/images with size filtering."""
    IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff')
    
    # Create output directory structure
    images_dir = os.path.join(output_base, "images")
    os.makedirs(images_dir, exist_ok=True)
    
    total_files = 0
    processed_files = 0
    
    def process_file(file_path):
        nonlocal processed_files
        filename = os.path.basename(file_path)
        _, ext = os.path.splitext(filename)
        ext = ext.lower()

        category_dir = None
        final_ext = None

        # Try extension match first
        if ext in IMAGE_EXTENSIONS:
            category_dir = "images"
            final_ext = ext[1:] if ext else 'jpg'
        else:
            # Magic byte detection fallback
            detected_cat, detected_ext = get_file_type(file_path)
            category_dir = detected_cat
            final_ext = detected_ext

        if category_dir == "images":
            # Apply size filter
            if not is_valid_image_size(file_path, min_size):
                print(f"[SKIPPED] {filename} (too small: < {min_size}x{min_size})")
                return
                
            target_folder = images_dir
            new_filename = f"{os.path.splitext(filename)[0]}.{final_ext}" if not ext else filename
            dst_file_path = os.path.join(target_folder, new_filename)
            
            # Handle duplicates
            if os.path.exists(dst_file_path):
                base, _ = os.path.splitext(new_filename)
                counter = 1
                while os.path.exists(os.path.join(target_folder, f"{base}_{counter}.{final_ext}")):
                    counter += 1
                new_filename = f"{base}_{counter}.{final_ext}"
                dst_file_path = os.path.join(target_folder, new_filename)
            
            try:
                shutil.copy2(file_path, dst_file_path)
                print(f"Copied: {filename} → images/{new_filename}")
                processed_files += 1
            except Exception as e:
                print(f"[ERROR] Failed copying {file_path}: {e}")

    # Process files in root directory
    direct_files = [f for f in os.listdir(source_path) 
                   if os.path.isfile(os.path.join(source_path, f))]
    if direct_files:
        print(f"[INFO] Found {len(direct_files)} files in root directory")
        total_files += len(direct_files)
        for file in direct_files:
            process_file(os.path.join(source_path, file))

    # Process files in subdirectories
    for root, dirs, files in os.walk(source_path):
        if root == source_path:
            continue
        
        if files:
            relative_path = os.path.relpath(root, source_path)
            print(f"[INFO] Processing directory: {relative_path}")
            total_files += len(files)
            
            for file in files:
                src_file_path = os.path.join(root, file)
                process_file(src_file_path)

    print(f"\n[EXTRACTION SUMMARY] Extracted {processed_files} images out of {total_files} total files")
    return processed_files

def encode_image_to_base64(image_path):
    """Convert image to base64 for Ollama."""
    try:
        with Image.open(image_path) as img:
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)
            
            # Validate PNG header
            png_bytes = buffer.getvalue()
            if not png_bytes.startswith(b'\x89PNG\r\n\x1A\n'):
                raise ValueError("Invalid PNG header")
            
            img_base64 = base64.b64encode(png_bytes).decode('utf-8')
            return f"data:image/png;base64,{img_base64}"
            
    except Exception as e:
        print(f"[ERROR] Encoding failed for {image_path}: {e}")
        return None

def classify_image_with_ollama(image_path, model_name, categories):
    """Classify image using Ollama."""
    normalized_path = image_path.replace('\\', '/')
    
    if not os.path.exists(image_path):
        print(f"[SKIP] File not found: {image_path}")
        return "other"
    
    abs_path = os.path.abspath(image_path)
    abs_normalized = abs_path.replace('\\', '/')
    
    prompt = (
        "Look at this image and classify it into exactly one category: "
        + ", ".join(categories) + ". "+
        "Category descriptions: "+
        "[birthdays]: Images depicting a special occasion with more festive theme like birthdays, Christmas or New years eve. Images commonly contain 'dressed up people', 'presents', 'diner guests'."+
        "[vacations]: Images depicting scenarios from vacation travels (hint: airport, suitcases). Commonly contain sunbathing and swimming (at the beach or by a pool) but also indicated by people looking warm in the hot climate or having a fresh sunburn."+
        "[family_photos]: Images depicting at least 2 persons that look to be closely related (hint: mom and dad, parent and child) in ordinary everyday scenarios."+
        "[child_photos]: Images depicting one or more children. Commonly the photo have a composition where the parent is behind the camera and capture something the child is doing."+
        "[nudity]: Images depicting naked genitals (penis or vagina) or breasts. If males have underwear on or women have both panties and a bra on its NOT classified as Nudity."+
        "[other]: Images that cant be matched to any of the descriptions in [birthdays], [vacations], [family_photos], [child_photos] or [nudity]."+
        "Base it on visible people, events, locations, or themes. "+
        "Respond with ONLY the category name (e.g., 'birthdays'). Do not explain."
    )

    try:
        response = ollama.chat(
            model=model_name,
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [abs_normalized]
            }]
        )
        predicted_category = response['message']['content'].strip().lower().replace('_', ' ')
        
        # Fuzzy match to categories
        for cat in categories:
            cat_normalized = cat.replace('_', ' ')
            if cat_normalized in predicted_category or predicted_category in cat_normalized:
                print(f"[DEBUG] {os.path.basename(image_path)} → '{predicted_category}' matched to '{cat}'")
                return cat
        print(f"[DEBUG] {os.path.basename(image_path)} → '{predicted_category}' no match, using 'other'")
        return "other"
        
    except Exception as e:
        print(f"[ERROR] Ollama classification failed for {image_path}: {e}")
        return "other"

def sort_images_by_category_ai(source, model_name, categories, min_size, dry_run=False, dest_root=None, progress_callback=None):
    """Main function: extract and classify images."""
    # Use source as extract folder, dest_root as output folder
    extract_folder = source
    output_folder = dest_root if dest_root else source
    
    # Create category folders
    for cat in categories:
        cat_folder = os.path.join(output_folder, cat)
        os.makedirs(cat_folder, exist_ok=True)
    
    # Step 1: Extract images
    if progress_callback:
        progress_callback(0, "Extracting images...", 0)
    
    print("[INFO] Starting image extraction...")
    extracted_count = extract_images(extract_folder, output_folder, min_size)
    
    if extracted_count == 0:
        return {cat: 0 for cat in categories}
    
    # Step 2: Classify images
    images_dir = os.path.join(output_folder, "images")
    if not os.path.exists(images_dir):
        return {cat: 0 for cat in categories}
    
    image_files = [f for f in os.listdir(images_dir) 
                  if os.path.isfile(os.path.join(images_dir, f)) and 
                  f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))]
    
    if not image_files:
        return {cat: 0 for cat in categories}
    
    print(f"[INFO] Found {len(image_files)} images to classify")
    results = {cat: 0 for cat in categories}
    
    # Process with progress updates
    for i, filename in enumerate(image_files):
        src_path = os.path.join(images_dir, filename)
        
        if progress_callback:
            percent = int((i / len(image_files)) * 100)
            progress_callback(len(image_files), filename, percent)
        
        if not os.path.exists(src_path):
            continue
            
        predicted_cat = classify_image_with_ollama(src_path, model_name, categories)
        
        # Move to category folder (unless dry run)
        dest_folder = os.path.join(output_folder, predicted_cat)
        dest_path = os.path.join(dest_folder, filename)
        
        # Handle duplicates
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest_path):
            new_filename = f"{base}_{counter}{ext}"
            dest_path = os.path.join(dest_folder, new_filename)
            counter += 1
        
        try:
            if not dry_run:
                shutil.move(src_path, dest_path)
            results[predicted_cat] += 1
            print(f"  → {filename} classified as '{predicted_cat}'")
        except Exception as e:
            print(f"[ERROR] Failed moving {filename}: {e}")
    
    # Cleanup empty images directory
    try:
        if not dry_run and os.path.exists(images_dir) and not os.listdir(images_dir):
            os.rmdir(images_dir)
            print(f"[CLEANUP] Removed empty images directory")
    except Exception as e:
        print(f"[WARNING] Could not remove images directory: {e}")
    
    if progress_callback:
        progress_callback(len(image_files), "Complete", 100)
    
    return results

def cleanup_empty_folders(path):
    """Clean up empty folders in the given path."""
    for root, dirs, files in os.walk(path, topdown=False):
        for dir in dirs:
            dir_path = os.path.join(root, dir)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    print(f"[CLEANUP] Removed empty folder: {dir_path}")
            except Exception as e:
                print(f"[WARNING] Could not remove folder {dir_path}: {e}")

# GUI Application
class ImageOrganizerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("🖼️ AI Image Organizer")
        self.root.geometry("600x500")
        
        # Variables
        self.source_var = tk.StringVar(value="")
        self.dest_var = tk.StringVar(value="")
        self.min_size_var = tk.IntVar(value=512)
        self.dry_run_var = tk.BooleanVar()
        self.run_cleanup_var = tk.BooleanVar(value=True)
        self.selected_cats = {cat: tk.BooleanVar(value=True) for cat in DEFAULT_CATEGORIES}
        
        self.progress = ttk.Progressbar(root, mode='determinate')
        self.status_label = tk.Label(root, text="Ready to organize!")
        
        self.setup_ui()
    
    def setup_ui(self):
        # Source & Dest
        ttk.Label(self.root, text="Source Directory:").pack(pady=5)
        source_frame = ttk.Frame(self.root)
        source_frame.pack(fill='x', padx=20)
        ttk.Entry(source_frame, textvariable=self.source_var).pack(side='left', fill='x', expand=True)
        ttk.Button(source_frame, text="Browse", command=self.browse_source).pack(side='right')
        
        ttk.Label(self.root, text="Output Directory:").pack(pady=5)
        dest_frame = ttk.Frame(self.root)
        dest_frame.pack(fill='x', padx=20)
        ttk.Entry(dest_frame, textvariable=self.dest_var).pack(side='left', fill='x', expand=True)
        ttk.Button(dest_frame, text="Browse", command=self.browse_dest).pack(side='right')
        
        # Min Size Slider
        ttk.Label(self.root, text="Min Image Size (px):").pack(pady=5)
        ttk.Scale(self.root, from_=128, to=1024, variable=self.min_size_var, orient='horizontal').pack(pady=5)
        ttk.Label(self.root, textvariable=self.min_size_var).pack()
        
        # Categories (Checkboxes)
        ttk.Label(self.root, text="Categories:").pack(pady=(20,5))
        cats_frame = ttk.Frame(self.root)
        cats_frame.pack(fill='x', padx=20)
        for cat in DEFAULT_CATEGORIES:
            cb = ttk.Checkbutton(cats_frame, text=cat.replace('_', ' ').title(), variable=self.selected_cats[cat])
            cb.pack(anchor='w')
        
        # Options
        options_frame = ttk.Frame(self.root)
        options_frame.pack(pady=10)
        ttk.Checkbutton(options_frame, text="Dry Run (Simulate)", variable=self.dry_run_var).pack(side='left')
        ttk.Checkbutton(options_frame, text="Cleanup After", variable=self.run_cleanup_var).pack(side='right')
        
        # Run Button
        ttk.Button(self.root, text="Run Organizer", command=self.run_organizer).pack(pady=20)
        
        # Progress & Status
        self.progress.pack(fill='x', padx=20, pady=5)
        self.status_label.pack()
    
    def browse_source(self):
        dir = filedialog.askdirectory()
        if dir:
            self.source_var.set(dir)
    
    def browse_dest(self):
        dir = filedialog.askdirectory()
        if dir:
            self.dest_var.set(dir)
    
    def progress_update(self, total, filename, percent):
        self.root.after(0, self.update_progress, percent)
        self.root.after(0, self.update_status, filename, percent)

    def update_progress(self, percent):
        self.progress['value'] = percent

    def update_status(self, filename, percent):
        self.status_label.config(text=f"Processing {os.path.basename(filename)} ({percent}%)")
    
    def run_organizer(self):
        source = self.source_var.get()
        dest = self.dest_var.get()
        min_size = self.min_size_var.get()
        dry_run = self.dry_run_var.get()
        categories = [cat for cat, var in self.selected_cats.items() if var.get()]
        
        if not os.path.exists(source):
            messagebox.showerror("Error", "Source directory not found!")
            return
        if not categories:
            messagebox.showerror("Error", "Select at least one category!")
            return
        
        # Use source as dest if not specified
        if not dest:
            dest = source
        
        # Thread for non-blocking run
        def threaded_run():
            self.progress['value'] = 0
            self.status_label.config(text="Starting extraction and classification...")
            results = sort_images_by_category_ai(
                source, MODEL_NAME, categories, min_size, dry_run, dest_root=dest,
                progress_callback=self.progress_update
            )
            if self.run_cleanup_var.get():
                cleanup_empty_folders(source)
            self.root.after(0, lambda: self.on_complete(results))
        
        threading.Thread(target=threaded_run, daemon=True).start()
    
    def on_complete(self, results):
        total = sum(results.values())
        msg = f"Complete! Sorted {total} images.\n" + "\n".join([f"{cat}: {count}" for cat, count in results.items()])
        messagebox.showinfo("Results", msg)
        self.status_label.config(text="Ready! Check output folders.")
        self.progress['value'] = 100

if __name__ == "__main__":
    root = tk.Tk()
    app = ImageOrganizerGUI(root)
    root.mainloop()