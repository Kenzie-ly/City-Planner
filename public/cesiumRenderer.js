function renderEntities(entities) {
    if (!viewer) return [];

    const safeColor = (colorStr, defaultColor = Cesium.Color.YELLOW) => {
        if (!colorStr) return defaultColor;
        try {
            const color = Cesium.Color.fromCssColorString(colorStr);
            return color || defaultColor;
        } catch (e) {
            return defaultColor;
        }
    };

    // 1. CLEAR PREVIOUS STATE
    viewer.entities.removeAll();
    viewer.dataSources.removeAll();
    viewer.clock.shouldAnimate = false;

    const addedEntities = [];
    const buildLabel = (entity, baseOptions = {}, defaultMode = 'always') => {
        const labelMode = String(entity.label_mode || defaultMode || 'always').toLowerCase();
        if (labelMode === 'hidden' || labelMode === 'hover') return undefined;
        const label = {
            font: '14px Inter, sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            showBackground: true,
            backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
            pixelOffset: new Cesium.Cartesian2(0, -30),
            horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
            ...baseOptions,
            text: String(entity.name || 'Entity'),
        };
        if (labelMode === 'zoom') {
            label.distanceDisplayCondition = new Cesium.DistanceDisplayCondition(0.0, 1400.0);
        }
        return label;
    };

    // 2. PRE-CALCULATE SIMULATION BOUNDS
    let globalMinStart = null;
    let globalMaxEnd = null;
    let hasSimulation = false;

    entities.forEach(entity => {
        if (entity.entity_type === 'simulation_vehicle') {
            hasSimulation = true;
            const startOffset = entity.startTimeOffset || 0;
            const startTime = Cesium.JulianDate.addSeconds(viewer.clock.startTime, startOffset, new Cesium.JulianDate());

            if (!globalMinStart || Cesium.JulianDate.compare(startTime, globalMinStart) < 0) {
                globalMinStart = startTime;
            }

            // Estimate duration
            let duration = 0;
            if (entity.path && entity.path.length > 1) {
                for (let i = 1; i < entity.path.length; i++) {
                    const p1 = Cesium.Cartesian3.fromDegrees(entity.path[i - 1].lng, entity.path[i - 1].lat);
                    const p2 = Cesium.Cartesian3.fromDegrees(entity.path[i].lng, entity.path[i].lat);
                    duration += Cesium.Cartesian3.distance(p1, p2) / ((entity.speed || 60) * (1000 / 3600));
                }
            }
            const endTime = Cesium.JulianDate.addSeconds(startTime, duration, new Cesium.JulianDate());
            if (!globalMaxEnd || Cesium.JulianDate.compare(endTime, globalMaxEnd) > 0) {
                globalMaxEnd = endTime;
            }
        }
    });

    // 3. CONFIGURE GLOBAL CLOCK IF SIMULATION EXISTS
    if (hasSimulation && globalMinStart && globalMaxEnd) {
        viewer.clock.startTime = globalMinStart;
        viewer.clock.stopTime = globalMaxEnd;
        viewer.clock.currentTime = globalMinStart;
        viewer.clock.multiplier = 1.0;
        viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP;
        viewer.clock.shouldAnimate = true;
        if (viewer.timeline) viewer.timeline.zoomTo(globalMinStart, globalMaxEnd);
    }

    // 4. ADD ENTITIES
    entities.forEach(entity => {
        let added = null;

        try {
            switch (entity.entity_type) {
                case 'polyline':
                case 'polyline_existing':
                case 'polyline_new': {
                    let segments = entity.polyline_positions || entity.path;
                    if (!segments || segments.length === 0) break;

                    // Ensure segments is a list of lists
                    if (!Array.isArray(segments[0])) {
                        segments = [segments];
                    }

                    const isBridge = (entity.entity_type === 'polyline_new');
                    const targetHeight = isBridge ? (entity.style && entity.style.height ? entity.style.height : 40) : 0;
                    const shouldClamp = !isBridge;
                    const baseWidth = isBridge ? 18 : 12;
                    
                    segments.forEach((positions, sIdx) => {
                        // VALIDATION: Ensure positions is an array
                        if (!Array.isArray(positions)) return;

                        const validPositions = positions.filter(p => p && typeof p.lng === 'number' && typeof p.lat === 'number');
                        if (validPositions.length < 2) return; // Cesium requires at least 2 points for a polyline!

                        const isExisting = (entity.entity_type === 'polyline_existing');
                        const isBridge = (entity.entity_type === 'polyline_new');
                        const targetHeight = isBridge ? ((entity.style && entity.style.height) ? entity.style.height : 40) : 0;
                        const shouldClamp = !isBridge;
                        const baseWidth = isBridge ? 18 : (isExisting ? 6 : 12);
                        const safeStyleColor = (entity.style && entity.style.color) ? entity.style.color : undefined;
                        const isPedestrian = (entity.name && (entity.name.toLowerCase().includes('pedestrian') || entity.name.toLowerCase().includes('walk')));
                        const color = safeColor(safeStyleColor, isPedestrian ? Cesium.Color.fromCssColorString('#10B981') : Cesium.Color.YELLOW);
                        const alpha = (entity.style && entity.style.alpha) ? entity.style.alpha : 0.8;

                        let material = color.withAlpha(alpha);
                        if (isExisting && entity.style && entity.style.dashed) {
                            material = new Cesium.PolylineDashMaterialProperty({
                                color: color.withAlpha(0.6),
                                dashLength: 16
                            });
                        }

                        if (isBridge) {
                            viewer.entities.add({
                                id: entity.id + "_base_" + sIdx,
                                polyline: {
                                    positions: Cesium.Cartesian3.fromDegreesArrayHeights(
                                        validPositions.flatMap(p => [p.lng, p.lat, (p.height || 0) + targetHeight - 2])
                                    ),
                                    width: baseWidth + 4,
                                    material: Cesium.Color.BLACK.withAlpha(0.5),
                                    clampToGround: false
                                }
                            });
                        }

                        const midIndex = Math.floor(validPositions.length / 2);
                        const mid = validPositions[midIndex];

                        const addedSeg = viewer.entities.add({
                            id: entity.id + "_" + sIdx,
                            name: entity.name,
                            position: Cesium.Cartesian3.fromDegrees(mid.lng, mid.lat, isBridge ? targetHeight + 20 : 0),
                            polyline: {
                                positions: Cesium.Cartesian3.fromDegreesArrayHeights(
                                    validPositions.flatMap(p => [p.lng, p.lat, (p.height || 0) + (isBridge ? targetHeight : 4)])
                                ),
                                width: baseWidth,
                                material: (() => {
                                    const styleHint = (entity.style && entity.style.hint) ? entity.style.hint.toLowerCase() : '';
                                    const isActive = isPedestrian || styleHint.includes('pedestrian');
                                    const baseColor = safeColor(safeStyleColor, isActive ? Cesium.Color.fromCssColorString('#10B981') : Cesium.Color.YELLOW);
                                    if (isActive) {
                                        return new Cesium.PolylineDashMaterialProperty({ color: baseColor, gapColor: Cesium.Color.TRANSPARENT, dashLength: 16.0, dashPattern: 255.0 });
                                    }
                                    return shouldClamp ?
                                        new Cesium.ColorMaterialProperty(baseColor.withAlpha(0.8)) :
                                        new Cesium.PolylineGlowMaterialProperty({ glowPower: 0.25, color: baseColor });
                                })(),
                                clampToGround: shouldClamp
                            },
                            label: sIdx === 0 ? buildLabel(entity, {
                                heightReference: shouldClamp ? Cesium.HeightReference.CLAMP_TO_GROUND : Cesium.HeightReference.NONE
                            }, entity.layer === 'context' || entity.layer === 'analysis' ? 'hidden' : 'always') : undefined
                        });

                        // Ensure ALL segments are tracked for cleanup/interaction
                        addedSeg.blurb = entity.blurb || '';
                        addedSeg.entityType = entity.entity_type;
                        addedEntities.push(addedSeg);

                        if (sIdx === 0) added = addedSeg;
                    });
                    break;
                }

                case 'point': {
                    if (!entity.position || typeof entity.position.lng !== 'number' || typeof entity.position.lat !== 'number') break;
                    added = viewer.entities.add({
                        id: entity.id,
                        name: entity.name,
                        position: Cesium.Cartesian3.fromDegrees(entity.position.lng, entity.position.lat, 0),
                        point: {
                            pixelSize: (entity.style && entity.style.pixelSize) ? entity.style.pixelSize : 16,
                            color: safeColor((entity.style && entity.style.color) ? entity.style.color : undefined, Cesium.Color.fromCssColorString('#3B82F6')).withAlpha((entity.style && (entity.style.alpha || entity.style.opacity)) ? (entity.style.alpha || entity.style.opacity) : 1.0),
                            outlineColor: Cesium.Color.WHITE,
                            outlineWidth: 2,
                            disableDepthTestDistance: Number.POSITIVE_INFINITY,
                            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                        },
                        label: buildLabel(entity, {
                            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                        }, entity.layer === 'context' || entity.layer === 'analysis' ? 'hidden' : 'always')
                    });
                    break;
                }

                case 'poi': {
                    // POI entities: lat/lon OR position.lat/position.lng
                    const lat = entity.lat ?? entity.position?.lat;
                    const lon = entity.lon ?? entity.lng ?? entity.position?.lng ?? entity.position?.lon;
                    if (lat == null || lon == null) break;

                    // Colour derived from style_hint string (e.g. "color:blue, size:large")
                    let poiColor = Cesium.Color.fromCssColorString('#9fb6ff');
                    const hintStr = String(entity.style_hint || (entity.style && entity.style.color) || '').toLowerCase();
                    if (hintStr.includes('blue'))   poiColor = Cesium.Color.fromCssColorString('#3B82F6');
                    else if (hintStr.includes('purple')) poiColor = Cesium.Color.fromCssColorString('#A78BFA');
                    else if (hintStr.includes('orange')) poiColor = Cesium.Color.fromCssColorString('#F97316');
                    else if (hintStr.includes('red'))    poiColor = Cesium.Color.fromCssColorString('#EF4444');
                    else if (hintStr.includes('green'))  poiColor = Cesium.Color.fromCssColorString('#10B981');
                    else if (hintStr.includes('cyan'))   poiColor = Cesium.Color.fromCssColorString('#06B6D4');
                    else if (hintStr.includes('yellow')) poiColor = Cesium.Color.fromCssColorString('#F59E0B');
                    else if (hintStr.includes('#'))      poiColor = safeColor(hintStr.match(/#[0-9a-f]{6}/i)?.[0], poiColor);

                    const pixelSize = hintStr.includes('large') ? 20 : hintStr.includes('small') ? 10 : 14;

                    added = viewer.entities.add({
                        id: entity.id,
                        name: entity.name || entity.label || 'Point of Interest',
                        position: Cesium.Cartesian3.fromDegrees(Number(lon), Number(lat), 0),
                        point: {
                            pixelSize: pixelSize,
                            color: poiColor,
                            outlineColor: Cesium.Color.WHITE,
                            outlineWidth: 2,
                            disableDepthTestDistance: Number.POSITIVE_INFINITY,
                            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                        },
                        label: buildLabel(
                            { ...entity, name: entity.name || entity.label || 'POI' },
                            { heightReference: Cesium.HeightReference.CLAMP_TO_GROUND },
                            entity.layer === 'context' || entity.layer === 'analysis' ? 'hidden' : 'always'
                        )
                    });
                    break;
                }

                case 'box': {
                    if (!entity.building || typeof entity.building.length !== 'number' || typeof entity.building.width !== 'number' || typeof entity.building.height !== 'number') break;
                    if (!entity.position || typeof entity.position.lng !== 'number' || typeof entity.position.lat !== 'number') break;
                    
                    added = viewer.entities.add({
                        id: entity.id,
                        name: entity.name,
                        position: Cesium.Cartesian3.fromDegrees(
                            entity.position.lng,
                            entity.position.lat,
                            (entity.position.height || 0) + (entity.building.height / 2)
                        ),
                        box: {
                            dimensions: new Cesium.Cartesian3(entity.building.length, entity.building.width, entity.building.height),
                            material: safeColor((entity.style && entity.style.color) ? entity.style.color : undefined, Cesium.Color.fromCssColorString('#FFD700')).withAlpha((entity.style && (entity.style.alpha || entity.style.opacity)) ? (entity.style.alpha || entity.style.opacity) : 0.6),
                            outline: true,
                            outlineColor: safeColor((entity.style && entity.style.color) ? entity.style.color : undefined, Cesium.Color.fromCssColorString('#FFD700'))
                        },
                        label: buildLabel(entity, {}, entity.layer === 'context' || entity.layer === 'analysis' ? 'hidden' : 'always')
                    });
                    break;
                }

                case 'polygon': {
                    if (!entity.polygon_positions || !Array.isArray(entity.polygon_positions) || entity.polygon_positions.length < 3) break;
                    
                    const validPts = entity.polygon_positions.filter(p => p && typeof p.lng === 'number' && typeof p.lat === 'number');
                    if (validPts.length < 3) break;

                    added = viewer.entities.add({
                        id: entity.id,
                        name: entity.name,
                        position: (() => {
                            const avgLat = validPts.reduce((sum, p) => sum + p.lat, 0) / validPts.length;
                            const avgLng = validPts.reduce((sum, p) => sum + p.lng, 0) / validPts.length;
                            return Cesium.Cartesian3.fromDegrees(avgLng, avgLat, ((entity.style && entity.style.height) ? entity.style.height : 0) + 10);
                        })(),
                        polygon: {
                            hierarchy: new Cesium.PolygonHierarchy(
                                Cesium.Cartesian3.fromDegreesArray(validPts.flatMap(p => [p.lng, p.lat]))
                            ),
                            material: safeColor((entity.style && entity.style.color) ? entity.style.color : undefined, Cesium.Color.fromCssColorString('#FFA500')).withAlpha((entity.style && (entity.style.alpha || entity.style.opacity)) ? (entity.style.alpha || entity.style.opacity) : 0.3),
                            outline: true,
                            outlineColor: safeColor((entity.style && entity.style.color) ? entity.style.color : undefined, Cesium.Color.fromCssColorString('#FFA500')),
                            clampToGround: true
                        },
                        label: buildLabel(entity, {
                            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                        }, entity.layer === 'context' || entity.layer === 'analysis' ? 'hidden' : 'always')
                    });
                    break;
                }

                case 'simulation_vehicle': {
                    if (!globalMinStart || !entity.path || entity.path.length < 2) break;
                    const startTime = globalMinStart;
                    const positionProperty = new Cesium.SampledPositionProperty();
                    let currentTime = Cesium.JulianDate.addSeconds(startTime, entity.startTimeOffset || 0, new Cesium.JulianDate());

                    entity.path.forEach((node, index) => {
                        if (!node || typeof node.lng !== 'number' || typeof node.lat !== 'number') return;
                        const pos = Cesium.Cartesian3.fromDegrees(node.lng, node.lat, (node.height || 0) + 2.5);
                        if (index === 0) {
                            positionProperty.addSample(currentTime, pos);
                        } else {
                            const prev = entity.path[index - 1];
                            const d = Cesium.Cartesian3.distance(Cesium.Cartesian3.fromDegrees(prev.lng, prev.lat), Cesium.Cartesian3.fromDegrees(node.lng, node.lat));
                            const dt = d / ((entity.speed || 60) * (1000 / 3600));
                            currentTime = Cesium.JulianDate.addSeconds(currentTime, dt, new Cesium.JulianDate());
                            positionProperty.addSample(currentTime, pos);
                        }
                    });

                    const availability = new Cesium.TimeIntervalCollection([
                        new Cesium.TimeInterval({
                            start: Cesium.JulianDate.addSeconds(startTime, entity.startTimeOffset || 0, new Cesium.JulianDate()),
                            stop: currentTime
                        })
                    ]);

                    added = viewer.entities.add({
                        id: entity.id,
                        name: entity.name,
                        availability: availability,
                        position: positionProperty,
                        orientation: new Cesium.VelocityOrientationProperty(positionProperty),
                        box: {
                            dimensions: getVehicleStyle(entity).dimensions,
                            material: getVehicleStyle(entity).material,
                            outline: true,
                            outlineColor: Cesium.Color.BLACK,
                            heightReference: Cesium.HeightReference.NONE
                        }
                    });
                    break;
                }
            }

            if (added) {
                added.blurb = entity.blurb || '';
                added.entityType = entity.entity_type;
                addedEntities.push(added);
            }
        } catch (entityError) {
            console.error("Failed to render entity:", entity, entityError);
        }
    });

    if (viewer.scene) viewer.scene.requestRender();
    return addedEntities;
}

function getVehicleStyle(entity) {
    let speed = entity.speed || 60;
    let flow = entity.flow || 'normal';
    let styleHint = (entity.style && entity.style.hint) ? entity.style.hint.toLowerCase() : '';
    let name = (entity.name) ? entity.name.toLowerCase() : '';

    // Default Car
    let dimensions = new Cesium.Cartesian3(8.0, 4.0, 3.0);
    let color = Cesium.Color.YELLOW;

    if (flow === 'congested' || speed < 30) color = Cesium.Color.fromCssColorString('#FF0000');
    else if (speed < 55) color = Cesium.Color.fromCssColorString('#FFBF00');
    else if (flow === 'optimized' || speed >= 55) color = Cesium.Color.fromCssColorString('#00FF7F');

    // Multi-Modal Overrides
    if (styleHint.includes('transit') || styleHint.includes('bus') || name.includes('bus') || name.includes('brt')) {
        dimensions = new Cesium.Cartesian3(12.0, 3.5, 4.0); // Longer and taller for Bus/Transit
        color = Cesium.Color.fromCssColorString('#3B82F6'); // Transit Blue
    } else if (styleHint.includes('train') || styleHint.includes('lrt') || styleHint.includes('mrt') || name.includes('train') || name.includes('lrt') || name.includes('mrt') || name.includes('ktm')) {
        dimensions = new Cesium.Cartesian3(24.0, 3.8, 4.5); // Very long for Train/MRT
        color = Cesium.Color.fromCssColorString('#A78BFA'); // Train Purple
    } else if (styleHint.includes('freight') || styleHint.includes('truck') || name.includes('freight') || name.includes('truck')) {
        dimensions = new Cesium.Cartesian3(14.0, 4.0, 4.5); // Large HGV
        color = Cesium.Color.fromCssColorString('#8B5A2B'); // Freight Brown
    } else if (styleHint.includes('pedestrian') || styleHint.includes('cycle') || name.includes('pedestrian') || name.includes('walk')) {
        dimensions = new Cesium.Cartesian3(2.0, 2.0, 2.0); // Small for humans/bikes
        color = Cesium.Color.fromCssColorString('#10B981'); // Active Mobility Green
    }

    return { dimensions: dimensions, material: color };
}

function flyToCamera(camera) {
    if (!viewer) return;
    viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(camera.center.lng, camera.center.lat, camera.height),
        orientation: {
            heading: Cesium.Math.toRadians(camera.heading ?? 0),
            pitch: Cesium.Math.toRadians(camera.pitch ?? -55),
            roll: Cesium.Math.toRadians(camera.roll ?? 0)
        },
        duration: 3.0
    });
}

/**
 * Clear all entities and data sources from the viewer.
 * Called before renderImplementationClusters when showMap is true.
 */
function clearAllEntities() {
    if (!viewer) return;
    viewer.entities.removeAll();
    viewer.dataSources.removeAll();
    viewer.clock.shouldAnimate = false;
}

/**
 * Fly to a lat/lng coordinate at a given altitude for the 3D building view.
 * @param {number} lat - Latitude
 * @param {number} lng - Longitude
 * @param {number} altitude - Height above ground in meters (default 600m for 3D view)
 * @param {string} label - Optional label for status display
 */
function flyToTarget(lat, lng, altitude = 600, label = 'Planning Site') {
    if (!viewer) return;
    viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(lng, lat, altitude),
        orientation: {
            heading: Cesium.Math.toRadians(0),
            pitch: Cesium.Math.toRadians(-45),
            roll: 0
        },
        duration: 2.5
    });
}
