# Satellite Tile Downloader

A Python tool for downloading and stitching satellite imagery tiles with proper georeferencing.

## Features

- Download satellite imagery tiles in parallel
- Support for multiple zoom levels
- Save tiles in JPG format with customizable quality
- Proper georeferencing with user-specified CRS
- Progress tracking with ETA
- Dynamic worker count optimization
- Output world files (.jgw) and auxiliary XML for GIS compatibility

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/Download_Tiles.git
cd Download_Tiles
```

2. Install the required packages:
```bash
pip install -r requirements.txt
```

## Usage

Run the script:
```bash
python main.py
```

You will be prompted to enter:
1. Top-left coordinate (latitude, longitude)
2. Bottom-right coordinate (latitude, longitude)
3. Zoom level (0-20)
4. CRS (default: EPSG:4326)

The script will:
1. Download tiles in parallel
2. Show download progress with ETA
3. Stitch tiles into a single image
4. Create proper georeferencing files

Output files will be in the `Output` folder:
- `stitched_satellite_zXX.jpg`: The stitched image
- `stitched_satellite_zXX.jgw`: World file with georeferencing
- `stitched_satellite_zXX.jpg.aux.xml`: Auxiliary XML with CRS information

## Project Structure

```
Download_Tiles/
├── main.py              # Main script
├── requirements.txt     # Python dependencies
├── Output/             # Stitched images and georeferencing files
└── Tiles/              # Downloaded tiles organized by zoom level
    └── Tiles_Zxx/     # Tiles for zoom level xx
```

## Requirements

See `requirements.txt` for Python package dependencies.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Uses Google Maps tile service for satellite imagery
- Built with Python and various open-source libraries
