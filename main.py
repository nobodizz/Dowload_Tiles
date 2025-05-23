# main.py
import os
import requests
import math
import concurrent.futures
import threading
import time
import random
from PIL import Image
from pyproj import Transformer, CRS
from datetime import datetime, timedelta

# Standard tile size for Web Mercator
TILE_SIZE = 256
ORIGIN_SHIFT = 20037508.342789244

def get_crs_info(crs_string):
    """Get CRS object and relevant information"""
    try:
        crs = CRS.from_string(crs_string)
        return crs
    except Exception as e:
        print(f"Warning: Invalid CRS '{crs_string}', falling back to EPSG:4326")
        return CRS.from_epsg(4326)

def tile_to_meters(x, y, zoom):
    """Convert tile coordinates to Web Mercator meters"""
    resolution = 2 * ORIGIN_SHIFT / (TILE_SIZE * 2**zoom)
    mx = x * TILE_SIZE * resolution - ORIGIN_SHIFT
    my = ORIGIN_SHIFT - y * TILE_SIZE * resolution
    return mx, my

def create_world_file(filename, x, y, zoom, tile_count_x, tile_count_y, target_crs):
    """Create a world file (.jgw) for georeferencing using the specified CRS"""
    # Get Web Mercator coordinates of tile corners
    resolution = 2 * ORIGIN_SHIFT / (TILE_SIZE * 2**zoom)
    mx, my = tile_to_meters(x, y, zoom)
    
    # Create transformers
    web_mercator = CRS.from_epsg(3857)
    
    if target_crs.is_geographic:
        # If target CRS is geographic (lat/lon), we need special handling
        transformer = Transformer.from_crs(web_mercator, target_crs)
        # Get the corners in target CRS
        top_left_x, top_left_y = transformer.transform(mx, my)
        # Calculate pixel size in degrees
        bottom_right_x, bottom_right_y = transformer.transform(
            mx + resolution * TILE_SIZE * tile_count_x,
            my - resolution * TILE_SIZE * tile_count_y
        )
        pixel_size_x = (bottom_right_x - top_left_x) / (TILE_SIZE * tile_count_x)
        pixel_size_y = (bottom_right_y - top_left_y) / (TILE_SIZE * tile_count_y)
    else:
        # For projected CRS
        transformer = Transformer.from_crs(web_mercator, target_crs)
        # Transform coordinates to target CRS
        top_left_x, top_left_y = transformer.transform(mx, my)
        # Calculate pixel size in target CRS units
        next_pixel_x, next_pixel_y = transformer.transform(mx + resolution, my - resolution)
        pixel_size_x = next_pixel_x - top_left_x
        pixel_size_y = next_pixel_y - top_left_y

    # Write world file
    world_filename = os.path.splitext(filename)[0] + '.jgw'
    with open(world_filename, 'w') as f:
        f.write(f"{pixel_size_x:.10f}\n")  # A: pixel size in x direction
        f.write("0.0000000000\n")          # B: rotation about y axis
        f.write("0.0000000000\n")          # C: rotation about x axis
        f.write(f"{pixel_size_y:.10f}\n")  # D: pixel size in y direction
        f.write(f"{top_left_x:.10f}\n")    # E: x coordinate of center of upper left pixel
        f.write(f"{top_left_y:.10f}\n")    # F: y coordinate of center of upper left pixel

def create_aux_xml(filename, target_crs):
    """Create auxiliary XML file with projection information for the specified CRS"""
    # Get WKT representation of the CRS
    wkt = target_crs.to_wkt()
    
    aux_xml = f"""<PAMDataset>
  <SRS>{wkt}</SRS>
  <GeoTransform>{filename}</GeoTransform>
</PAMDataset>"""
    
    # Write auxiliary XML file
    aux_filename = filename + '.aux.xml'
    with open(aux_filename, 'w') as f:
        f.write(aux_xml)

# Lock for thread-safe access to the downloaded_tiles_count and timing variables
progress_lock = threading.Lock()
downloaded_tiles_count = 0
total_tiles_to_download = 0
start_time = None
last_update_time = None
download_rates = []

def calculate_eta():
    """Calculate estimated time remaining based on current download rate"""
    global start_time, last_update_time, download_rates
    
    if start_time is None or downloaded_tiles_count == 0:
        return "Calculating..."
    
    current_time = time.time()
    
    # Calculate current download rate (tiles per second)
    elapsed_time = current_time - start_time
    current_rate = downloaded_tiles_count / elapsed_time if elapsed_time > 0 else 0
    
    # Keep track of recent download rates for better estimation
    download_rates.append(current_rate)
    if len(download_rates) > 5:  # Keep only last 5 rates
        download_rates.pop(0)
    
    # Use average of recent rates for estimation
    avg_rate = sum(download_rates) / len(download_rates)
    
    if avg_rate > 0:
        remaining_tiles = total_tiles_to_download - downloaded_tiles_count
        seconds_remaining = remaining_tiles / avg_rate
        eta = datetime.now() + timedelta(seconds=seconds_remaining)
        
        if seconds_remaining < 60:
            return f"{int(seconds_remaining)} seconds"
        elif seconds_remaining < 3600:
            minutes = int(seconds_remaining / 60)
            return f"{minutes} minutes"
        else:
            hours = int(seconds_remaining / 3600)
            minutes = int((seconds_remaining % 3600) / 60)
            return f"{hours} hours {minutes} minutes"
    
    return "Calculating..."

def update_progress():
    """Update and display download progress with ETA"""
    if downloaded_tiles_count > 0:
        eta = calculate_eta()
        progress = (downloaded_tiles_count / total_tiles_to_download) * 100
        print(f"Downloaded: {downloaded_tiles_count}/{total_tiles_to_download} ({progress:.1f}%) - ETA: {eta}", end='\r')

def get_script_dir():
    """Returns the directory containing this script"""
    return os.path.dirname(os.path.abspath(__file__))

def get_tiles_dir(zoom_level):
    """Returns the directory for storing tiles at a specific zoom level"""
    base_dir = os.path.join(get_script_dir(), "Tiles", f"Tiles_Z{zoom_level}")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir

def get_output_dir():
    """Returns the directory for storing final stitched images"""
    output_dir = os.path.join(get_script_dir(), "Output")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir

def latlon_to_tile(lat, lon, zoom):
    """Converts geographic coordinates to tile indices"""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x_tile = int(math.floor((lon + 180.0) / 360.0 * n))
    y_tile = int(math.floor((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n))
    return x_tile, y_tile

def get_optimal_workers(total_tiles):
    """Returns the optimal number of workers based on total tiles to download"""
    if total_tiles < 100:
        return min(15, total_tiles)  # For small areas
    elif total_tiles < 500:
        return 10  # For medium areas
    else:
        return 8  # For large areas

def download_tile(tile_info, base_url, zoom_level):
    """Downloads a single tile and updates progress"""
    global downloaded_tiles_count, total_tiles_to_download, start_time, last_update_time

    x, y = tile_info
    tile_url = base_url.format(x=x, y=y, z=zoom_level)
    tiles_dir = get_tiles_dir(zoom_level)
    tile_filename = os.path.join(tiles_dir, f"{zoom_level}_{x}_{y}.jpg")

    if os.path.exists(tile_filename):
        with progress_lock:
            downloaded_tiles_count += 1
            update_progress()
        return

    try:
        # Add a small random delay between requests
        time.sleep(0.2 + (0.3 * random.random()))  # Random delay between 0.2 and 0.5 seconds
        
        response = requests.get(tile_url, stream=True, timeout=10)
        response.raise_for_status()

        # Save the downloaded image as JPG with 90% quality
        img = Image.open(response.raw)
        img.save(tile_filename, 'JPEG', quality=90)

        with progress_lock:
            if start_time is None:
                start_time = time.time()
            downloaded_tiles_count += 1
            last_update_time = time.time()
            update_progress()

    except requests.exceptions.RequestException as e:
        with progress_lock:
            print(f"\nError downloading tile {tile_url}: {e}")
    except Exception as e:
        with progress_lock:
            print(f"\nAn unexpected error occurred: {e}")

def stitch_tiles(min_x, max_x, min_y, max_y, zoom_level, crs_string):
    """Stitches downloaded tiles into a single image and adds georeferencing"""
    tile_width = TILE_SIZE
    tile_height = TILE_SIZE

    img_width = (max_x - min_x + 1) * tile_width
    img_height = (max_y - min_y + 1) * tile_height

    if img_width <= 0 or img_height <= 0:
        print("\nNo valid tiles to stitch.")
        return

    try:
        # Parse the target CRS
        target_crs = get_crs_info(crs_string)
        
        stitched_image = Image.new('RGB', (img_width, img_height))
        print(f"\nStitching {img_width//tile_width * img_height//tile_height} tiles...")

        tiles_dir = get_tiles_dir(zoom_level)
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                tile_filename = os.path.join(tiles_dir, f"{zoom_level}_{x}_{y}.jpg")
                try:
                    with Image.open(tile_filename) as img:
                        paste_x = (x - min_x) * tile_width
                        paste_y = (y - min_y) * tile_height
                        stitched_image.paste(img, (paste_x, paste_y))
                except FileNotFoundError:
                    pass

        output_dir = get_output_dir()
        current_date = datetime.now().strftime("%Y-%m-%d")
        stitched_image_path = os.path.join(output_dir, f"{current_date}_z{zoom_level}.jpg")
        stitched_image.save(stitched_image_path, 'JPEG', quality=90)
        
        # Add georeferencing with the specified CRS
        create_world_file(stitched_image_path, min_x, min_y, zoom_level,
                         max_x - min_x + 1, max_y - min_y + 1, target_crs)
        create_aux_xml(stitched_image_path, target_crs)
        
        print(f"Stitched image saved to: {stitched_image_path}")
        print(f"Added georeferencing information ({target_crs.name})")
    except Exception as e:
        print(f"\nError during stitching: {e}")

def download_satellite_tiles(top_left_coord, bottom_right_coord, zoom_level, crs):
    """Main function to download and process tiles"""
    global downloaded_tiles_count, total_tiles_to_download, start_time, last_update_time, download_rates
    
    # Reset tracking variables
    downloaded_tiles_count = 0
    total_tiles_to_download = 0
    start_time = None
    last_update_time = None
    download_rates = []

    print(f"\nDownloading tiles for area: {top_left_coord} to {bottom_right_coord}")
    print(f"Zoom level: {zoom_level}, CRS: {crs}")

    # Convert coordinates to tile indices
    top_left_tile_x, top_left_tile_y = latlon_to_tile(*top_left_coord, zoom_level)
    bottom_right_tile_x, bottom_right_tile_y = latlon_to_tile(*bottom_right_coord, zoom_level)

    # Calculate tile range
    min_tile_x = min(top_left_tile_x, bottom_right_tile_x)
    max_tile_x = max(top_left_tile_x, bottom_right_tile_x)
    min_tile_y = min(top_left_tile_y, bottom_right_tile_y)
    max_tile_y = max(top_left_tile_y, bottom_right_tile_y)

    # Calculate total tiles and optimal worker count
    total_tiles_to_download = (max_tile_x - min_tile_x + 1) * (max_tile_y - min_tile_y + 1)
    optimal_workers = get_optimal_workers(total_tiles_to_download)
    print(f"Total tiles to download: {total_tiles_to_download}")
    print(f"Using {optimal_workers} parallel workers")

    # Download tiles in parallel
    base_url = "https://www.google.com/maps/vt?lyrs=s@180&gl=cn&x={x}&y={y}&z={z}"
    with concurrent.futures.ThreadPoolExecutor(max_workers=optimal_workers) as executor:
        futures = []
        for y in range(min_tile_y, max_tile_y + 1):
            for x in range(min_tile_x, max_tile_x + 1):
                futures.append(executor.submit(
                    download_tile, (x, y), base_url, zoom_level
                ))
        
        for future in concurrent.futures.as_completed(futures):
            pass  # Errors are handled in download_tile

    print("\nDownload complete!")
      # Stitch tiles if any were downloaded
    if downloaded_tiles_count > 0:
        stitch_tiles(min_tile_x, max_tile_x, min_tile_y, max_tile_y, zoom_level, crs)
    else:
        print("No tiles were downloaded - nothing to stitch")

if __name__ == "__main__":
    print("Satellite Image Downloader")
    print("-------------------------")
    
    # Get coordinates
    def get_coord(prompt):
        while True:
            coord_str = input(prompt)
            try:
                lat, lon = map(float, coord_str.replace(' ', '').split(','))
                return (lat, lon)
            except ValueError:
                print("Invalid format. Use: latitude,longitude (e.g., 37.427,-122.145)")

    top_left = get_coord("Enter Top-Left coordinate (lat,lon): ")
    bottom_right = get_coord("Enter Bottom-Right coordinate (lat,lon): ")

    # Get zoom level
    while True:
        try:
            zoom = int(input("Enter Zoom Level (0-20): "))
            if 0 <= zoom <= 20:
                break
            print("Zoom must be between 0 and 20")
        except ValueError:
            print("Please enter an integer")

    crs = input("Enter CRS (default EPSG:4326): ") or "EPSG:4326"

    # Reset counters and run
    downloaded_tiles_count = 0
    download_satellite_tiles(top_left, bottom_right, zoom, crs)

    print("\nProcessing complete!")
    print("Note: Be mindful of tile provider's terms of service")
