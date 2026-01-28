from qgis.core import (
    QgsVectorLayer, QgsProject, QgsFeature, QgsGeometry, QgsPointXY,
    QgsVectorFileWriter, QgsFields, QgsField, QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant
import processing
import os

print("=" * 80)
print("GEOARCHITECT: River Habitat Suitability Analysis (OPTIMIZED)")
print("=" * 80)

# ============================================================================
# CONFIGURATION
# ============================================================================
SEGMENT_LENGTH = 4000      # 4km in meters
SAMPLE_INTERVAL = 50       # Create segments every 50m for maximum coverage
FOREST_BUFFER = 20         # 20m buffer on forest polygons
MIN_FOREST_LENGTH = 1900   # 1.9km minimum forest requirement
OUTPUT_PATH = "D:/CI-Archified/Fiver/Tesing Output/"

# Create output directory if it doesn't exist
import os
if not os.path.exists(OUTPUT_PATH):
    os.makedirs(OUTPUT_PATH)
    print(f"Created output directory: {OUTPUT_PATH}")

# ============================================================================
# STEP 1: Get Input Layers
# ============================================================================
print("\n[1/5] Loading input layers...")

try:
    river_layer = QgsProject.instance().mapLayersByName('Freshwater lines merged')[0]
    forest_layer = QgsProject.instance().mapLayersByName('Deciduous forest')[0]
    print(f"✓ River: {river_layer.featureCount()} features, CRS: {river_layer.crs().authid()}")
    print(f"✓ Forest: {forest_layer.featureCount()} features")
except IndexError:
    print("ERROR: Layers not found! Ensure 'Freshwater lines merged' and 'Deciduous forest' are loaded.")
    raise

# ============================================================================
# STEP 2: Buffer Forest by 20m
# ============================================================================
print(f"\n[2/5] Buffering forest by {FOREST_BUFFER}m...")

buffered_forest = processing.run("native:buffer", {
    'INPUT': forest_layer,
    'DISTANCE': FOREST_BUFFER,
    'SEGMENTS': 5,
    'DISSOLVE': True,  # Merge all forest into one geometry for faster processing
    'OUTPUT': 'memory:'
})['OUTPUT']

print(f"✓ Forest buffered and dissolved")

# Get buffered forest as single geometry
forest_geoms = [f.geometry() for f in buffered_forest.getFeatures()]
if forest_geoms:
    buffered_forest_geom = QgsGeometry.unaryUnion(forest_geoms)
    print(f"✓ Created unified forest geometry")
else:
    print("ERROR: No forest geometry found!")
    raise ValueError("Empty forest layer")

# ============================================================================
# STEP 3: Generate 4km Segments (Linear Sampling)
# ============================================================================
print(f"\n[3/5] Generating 4km segments every {SAMPLE_INTERVAL}m (high density)...")

# Setup output fields
segment_fields = QgsFields()
segment_fields.append(QgsField('seg_id', QVariant.Int))
segment_fields.append(QgsField('start_m', QVariant.Double))
segment_fields.append(QgsField('end_m', QVariant.Double))
segment_fields.append(QgsField('length_m', QVariant.Double))
segment_fields.append(QgsField('forest_m', QVariant.Double))
segment_fields.append(QgsField('forest_km', QVariant.Double))
segment_fields.append(QgsField('suitable', QVariant.String, len=3))

# Create output layer
results_layer = QgsVectorLayer(
    f"LineString?crs={river_layer.crs().authid()}", 
    "Suitable River Segments", 
    "memory"
)
results_layer.dataProvider().addAttributes(segment_fields)
results_layer.updateFields()

segment_id = 1
suitable_count = 0
total_segments = 0

# Process each river feature
for river_feat in river_layer.getFeatures():
    river_geom = river_feat.geometry()
    
    # Handle MultiLineString
    if river_geom.isMultipart():
        lines = river_geom.asMultiPolyline()
    else:
        lines = [river_geom.asPolyline()]
    
    # Process each line part
    for line in lines:
        line_geom = QgsGeometry.fromPolylineXY(line)
        total_length = line_geom.length()
        
        # Skip if line is shorter than 4km
        if total_length < SEGMENT_LENGTH:
            continue
        
        # Sample along line
        start_dist = 0
        
        while start_dist + SEGMENT_LENGTH <= total_length:
            # Extract 4km segment
            points = []
            
            # Sample points every 50m along the segment for smooth geometry
            for dist in range(int(start_dist), int(start_dist + SEGMENT_LENGTH) + 50, 50):
                if dist > start_dist + SEGMENT_LENGTH:
                    dist = start_dist + SEGMENT_LENGTH
                
                point_geom = line_geom.interpolate(dist)
                if point_geom and not point_geom.isEmpty():
                    pt = point_geom.asPoint()
                    points.append(QgsPointXY(pt.x(), pt.y()))
                
                if dist >= start_dist + SEGMENT_LENGTH:
                    break
            
            # Create segment geometry
            if len(points) >= 2:
                segment_geom = QgsGeometry.fromPolylineXY(points)
                segment_length = segment_geom.length()
                
                # Calculate forest intersection - MUST sum all fragments
                forest_length = 0
                if segment_geom.intersects(buffered_forest_geom):
                    forest_intersection = segment_geom.intersection(buffered_forest_geom)
                    
                    if not forest_intersection.isEmpty():
                        # Handle MultiLineString (fragmented forest along river)
                        if forest_intersection.isMultipart():
                            # Sum lengths of all individual forest fragments
                            for part in forest_intersection.asMultiPolyline():
                                part_geom = QgsGeometry.fromPolylineXY(part)
                                forest_length += part_geom.length()
                        else:
                            # Single continuous forest section
                            forest_length = forest_intersection.length()
                
                forest_km = round(forest_length / 1000, 3)
                is_suitable = "YES" if forest_length >= MIN_FOREST_LENGTH else "NO"
                
                if is_suitable == "YES":
                    suitable_count += 1
                
                # Create feature
                feat = QgsFeature()
                feat.setGeometry(segment_geom)
                feat.setAttributes([
                    segment_id,
                    round(start_dist, 1),
                    round(start_dist + SEGMENT_LENGTH, 1),
                    round(segment_length, 1),
                    round(forest_length, 1),
                    forest_km,
                    is_suitable
                ])
                
                results_layer.dataProvider().addFeature(feat)
                segment_id += 1
                total_segments += 1
                
                # Progress indicator
                if total_segments % 25 == 0:  # Report more frequently due to higher volume
                    print(f"  Processed {total_segments} segments, {suitable_count} suitable...")
            
            start_dist += SAMPLE_INTERVAL

results_layer.updateExtents()
print(f"✓ Generated {total_segments} segments, {suitable_count} suitable")

# ============================================================================
# STEP 4: Save All Segments 
# ============================================================================
print(f"\n[4/5] Saving complete segment inventory...")

all_segments_output = OUTPUT_PATH + "river_segments_4km_all.shp"
QgsVectorFileWriter.writeAsVectorFormat(
    results_layer,
    all_segments_output,
    "UTF-8",
    river_layer.crs(),
    "ESRI Shapefile"
)
print(f"✓ Saved: {all_segments_output}")

# ============================================================================
# STEP 5: Create Filtered Layer (Only Suitable Segments)
# ============================================================================
print(f"\n[5/5] Creating suitable-only layer...")

# Filter for suitable segments
suitable_layer = processing.run("native:extractbyattribute", {
    'INPUT': results_layer,
    'FIELD': 'suitable',
    'OPERATOR': 0,  # equals
    'VALUE': 'YES',
    'OUTPUT': 'memory:'
})['OUTPUT']

suitable_output = OUTPUT_PATH + "suitable_segments.shp"
QgsVectorFileWriter.writeAsVectorFormat(
    suitable_layer,
    suitable_output,
    "UTF-8",
    river_layer.crs(),
    "ESRI Shapefile"
)
print(f"✓ Saved: {suitable_output}")

# Add to project
QgsProject.instance().addMapLayer(results_layer)
QgsProject.instance().addMapLayer(suitable_layer)

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "=" * 80)
print("ANALYSIS COMPLETE!")
print("=" * 80)
print(f"Total 4km segments analyzed: {total_segments}")
print(f"Segments meeting ≥1.9km forest criterion: {suitable_count}")
print(f"Success rate: {round(suitable_count/total_segments*100, 1) if total_segments > 0 else 0}%")
print(f"\nOutput files:")
print(f"  1. {all_segments_output} (all segments)")
print(f"  2. {suitable_output} (suitable segments only)")
print("=" * 80)