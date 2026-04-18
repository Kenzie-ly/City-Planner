function renderEntities(entities) {
    const addedEntities = [];

    entities.forEach(entity => {
        let added = null;

        switch (entity.entity_type) {

            case 'polyline': {
                const midIndex = Math.floor(entity.polyline_positions.length / 2);
                const mid = entity.polyline_positions[midIndex];
                added = viewer.entities.add({
                    id: entity.id,
                    name: entity.name,
                    position: Cesium.Cartesian3.fromDegrees(mid.lng, mid.lat, 80),
                    polyline: {
                        positions: Cesium.Cartesian3.fromDegreesArrayHeights(
                            entity.polyline_positions.flatMap(p => [
                                p.lng, p.lat, (p.height ?? 0) + 60
                            ])
                        ),
                        width: entity.style.width ?? 10,
                        material: new Cesium.PolylineGlowMaterialProperty({
                            glowPower: 0.2,
                            color: Cesium.Color.fromCssColorString(entity.style.color ?? '#FFD700')
                        }),
                        clampToGround: false
                    },
                    label: {
                        text: String(entity.name), // ✅ force string
                        font: '14px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        showBackground: true,
                        backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        pixelOffset: new Cesium.Cartesian2(0, -20),
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM
                    }
                });

                // ADD THESE TWO LINES after every viewer.entities.add()
                added.blurb = entity.blurb ?? '';
                added.entityType = entity.entity_type;
                break;
            }

            case 'point': {
                added = viewer.entities.add({
                    id: entity.id,
                    name: entity.name,
                    position: Cesium.Cartesian3.fromDegrees(
                        entity.position.lng,
                        entity.position.lat,
                        entity.position.height ?? 0
                    ),
                    point: {
                        pixelSize: 16,
                        color: Cesium.Color.fromCssColorString(entity.style.color),
                        outlineColor: Cesium.Color.WHITE,
                        outlineWidth: 2,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY
                    },
                    label: {
                        text: String(entity.name), // ✅ force string
                        font: '14px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        showBackground: true,
                        backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                        pixelOffset: new Cesium.Cartesian2(0, -30),
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM
                    }
                });

                // ADD THESE TWO LINES after every viewer.entities.add()
                added.blurb = entity.blurb ?? '';
                added.entityType = entity.entity_type;
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
                        dimensions: new Cesium.Cartesian3(
                            entity.building.length,
                            entity.building.width,
                            entity.building.height
                        ),
                        material: Cesium.Color.fromCssColorString(entity.style.color).withAlpha(0.6),
                        outline: true,
                        outlineColor: Cesium.Color.fromCssColorString(entity.style.color)
                    },
                    label: {
                        text: String(entity.name), // ✅ force string
                        font: '14px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        showBackground: true,
                        backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                        pixelOffset: new Cesium.Cartesian2(0, -35),
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM
                    }
                });

                // ADD THESE TWO LINES after every viewer.entities.add()
                added.blurb = entity.blurb ?? '';
                added.entityType = entity.entity_type;
                break;
            }

            case 'polygon': { // ✅ NEW - was completely missing
                added = viewer.entities.add({
                    id: entity.id,
                    name: entity.name,
                    polygon: {
                        hierarchy: new Cesium.PolygonHierarchy(
                            Cesium.Cartesian3.fromDegreesArray(
                                entity.polygon_positions.flatMap(p => [p.lng, p.lat])
                            )
                        ),
                        material: Cesium.Color.fromCssColorString(
                            entity.style.color ?? '#FFA500'
                        ).withAlpha(entity.style.opacity ?? 0.3),
                        outline: true,
                        outlineColor: Cesium.Color.fromCssColorString(
                            entity.style.color ?? '#FFA500'
                        ),
                        outlineWidth: 2,
                        clampToGround: true
                    },
                    label: {
                        text: String(entity.name),
                        font: '14px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        showBackground: true,
                        backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM
                    }
                });

                // ADD THESE TWO LINES after every viewer.entities.add()
                added.blurb = entity.blurb ?? '';
                added.entityType = entity.entity_type;
                break;
            }
        }

        if (added) addedEntities.push(added);
    });

    return addedEntities;
}

function flyToCamera(camera) {
    viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(
            camera.center.lng,
            camera.center.lat,
            camera.height
        ),
        orientation: {
            heading: Cesium.Math.toRadians(camera.heading ?? 0),
            pitch: Cesium.Math.toRadians(camera.pitch ?? -55),
            roll: Cesium.Math.toRadians(camera.roll ?? 0)
        },
        duration: 3.0
    });
}