function renderEntities(entities) {
    if (!viewer) return [];
    
    // 1. CLEAR PREVIOUS STATE
    viewer.entities.removeAll();
    viewer.dataSources.removeAll();
    viewer.clock.shouldAnimate = false;
    
    const addedEntities = [];

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
                    const p1 = Cesium.Cartesian3.fromDegrees(entity.path[i-1].lng, entity.path[i-1].lat);
                    const p2 = Cesium.Cartesian3.fromDegrees(entity.path[i].lng, entity.path[i].lat);
                    duration += Cesium.Cartesian3.distance(p1, p2) / ((entity.speed || 60) * (1000/3600));
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

        switch (entity.entity_type) {
            case 'polyline':
            case 'polyline_existing':
            case 'polyline_new': {
                let segments = entity.polyline_positions;
                if (!segments || segments.length === 0) break;
                
                // Ensure segments is a list of lists
                if (!Array.isArray(segments[0])) {
                    segments = [segments];
                }

                const isBridge = (entity.entity_type === 'polyline_new');
                const targetHeight = isBridge ? (entity.style.height ?? 40) : 0;
                const shouldClamp = !isBridge;
                const baseWidth = isBridge ? 18 : 12;
                segments.forEach((positions, sIdx) => {
                    // VALIDATION: Ensure positions is an array of valid coordinate objects
                    if (!Array.isArray(positions) || positions.length < 2) return;
                    
                    const validPositions = positions.filter(p => p && typeof p.lng === 'number' && typeof p.lat === 'number');
                    const isExisting = (entity.entity_type === 'polyline_existing');
                    const isBridge = (entity.entity_type === 'polyline_new');
                    const targetHeight = isBridge ? (entity.style.height ?? 40) : 0;
                    const shouldClamp = !isBridge;
                    const baseWidth = isBridge ? 18 : (isExisting ? 6 : 12);
                    const color = entity.style.color ? Cesium.Color.fromCssColorString(entity.style.color) : Cesium.Color.YELLOW;
                    const alpha = entity.style.alpha ?? 0.8;

                    let material = color.withAlpha(alpha);
                    if (isExisting && entity.style.dashed) {
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
                                    validPositions.flatMap(p => [p.lng, p.lat, (p.height ?? 0) + targetHeight - 2])
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
                                validPositions.flatMap(p => [p.lng, p.lat, (p.height ?? 0) + (isBridge ? targetHeight : 4)])
                            ),
                            width: baseWidth,
                            material: (() => {
                                const styleHint = entity.style.hint ? entity.style.hint.toLowerCase() : '';
                                const isActive = (entity.name && (entity.name.toLowerCase().includes('pedestrian') || entity.name.toLowerCase().includes('walk'))) || styleHint.includes('pedestrian');
                                const baseColor = Cesium.Color.fromCssColorString(entity.style.color ?? (isActive ? '#10B981' : '#FFD700'));
                                if (isActive) {
                                    return new Cesium.PolylineDashMaterialProperty({ color: baseColor, gapColor: Cesium.Color.TRANSPARENT, dashLength: 16.0, dashPattern: 255.0 });
                                }
                                return shouldClamp ? 
                                    new Cesium.ColorMaterialProperty(baseColor.withAlpha(0.8)) :
                                    new Cesium.PolylineGlowMaterialProperty({ glowPower: 0.25, color: baseColor });
                            })(),
                            clampToGround: shouldClamp
                        },
                        label: sIdx === 0 ? {
                            text: String(entity.name),
                            font: '14px sans-serif',
                            fillColor: Cesium.Color.WHITE,
                            outlineColor: Cesium.Color.BLACK,
                            outlineWidth: 2,
                            showBackground: true,
                            backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                            disableDepthTestDistance: Number.POSITIVE_INFINITY,
                            pixelOffset: new Cesium.Cartesian2(0, -20),
                            horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                            verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                            heightReference: shouldClamp ? Cesium.HeightReference.CLAMP_TO_GROUND : Cesium.HeightReference.NONE
                        } : undefined
                    });

                    // Ensure ALL segments are tracked for cleanup/interaction
                    addedSeg.blurb = entity.blurb ?? '';
                    addedSeg.entityType = entity.entity_type;
                    addedEntities.push(addedSeg);
                    
                    if (sIdx === 0) added = addedSeg;
                });
                break;
            }

            case 'point': {
                added = viewer.entities.add({
                    id: entity.id,
                    name: entity.name,
                    position: Cesium.Cartesian3.fromDegrees(entity.position.lng, entity.position.lat, 0),
                    point: {
                        pixelSize: 16,
                        color: Cesium.Color.fromCssColorString(entity.style.color),
                        outlineColor: Cesium.Color.WHITE,
                        outlineWidth: 2,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                    },
                    label: {
                        text: String(entity.name),
                        font: '14px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        showBackground: true,
                        backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                        pixelOffset: new Cesium.Cartesian2(0, -30),
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                    }
                });
                break;
            }

            case 'box': {
                added = viewer.entities.add({
                    id: entity.id,
                    name: entity.name,
                    position: Cesium.Cartesian3.fromDegrees(
                        entity.position.lng,
                        entity.position.lat,
                        (entity.position.height ?? 0) + (entity.building.height / 2)
                    ),
                    box: {
                        dimensions: new Cesium.Cartesian3(entity.building.length, entity.building.width, entity.building.height),
                        material: Cesium.Color.fromCssColorString(entity.style.color).withAlpha(0.6),
                        outline: true,
                        outlineColor: Cesium.Color.fromCssColorString(entity.style.color)
                    },
                    label: {
                        text: String(entity.name),
                        font: '14px sans-serif',
                        pixelOffset: new Cesium.Cartesian2(0, -35),
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM
                    }
                });
                break;
            }

            case 'polygon': {
                added = viewer.entities.add({
                    id: entity.id,
                    name: entity.name,
                    polygon: {
                        hierarchy: new Cesium.PolygonHierarchy(
                            Cesium.Cartesian3.fromDegreesArray(entity.polygon_positions.flatMap(p => [p.lng, p.lat]))
                        ),
                        material: Cesium.Color.fromCssColorString(entity.style.color ?? '#FFA500').withAlpha(entity.style.opacity ?? 0.3),
                        outline: true,
                        outlineColor: Cesium.Color.fromCssColorString(entity.style.color ?? '#FFA500'),
                        clampToGround: true
                    },
                    label: {
                        text: String(entity.name),
                        font: '14px sans-serif',
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                    }
                });
                break;
            }

            case 'simulation_vehicle': {
                if (!globalMinStart) break;
                const startTime = globalMinStart;
                const positionProperty = new Cesium.SampledPositionProperty();
                let currentTime = Cesium.JulianDate.addSeconds(startTime, entity.startTimeOffset || 0, new Cesium.JulianDate());

                entity.path.forEach((node, index) => {
                    const pos = Cesium.Cartesian3.fromDegrees(node.lng, node.lat, (node.height || 0) + 2.5);
                    if (index === 0) {
                        positionProperty.addSample(currentTime, pos);
                    } else {
                        const prev = entity.path[index - 1];
                        const d = Cesium.Cartesian3.distance(Cesium.Cartesian3.fromDegrees(prev.lng, prev.lat), Cesium.Cartesian3.fromDegrees(node.lng, node.lat));
                        const dt = d / ((entity.speed || 60) * (1000/3600));
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
            added.blurb = entity.blurb ?? '';
            added.entityType = entity.entity_type;
            addedEntities.push(added);
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