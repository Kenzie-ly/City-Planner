import osmnx as ox
from collections import Counter

path = "data/graphs/selangor_walk.graphml"

G = ox.load_graphml(path)

highways = []
for _, _, _, data in G.edges(keys=True, data=True):
    highway = data.get("highway")
    if isinstance(highway, list):
        highways.extend(highway)
    elif highway:
        highways.append(highway)

print("Nodes:", len(G.nodes))
print("Edges:", len(G.edges))
print("Top highway types:")
print(Counter(highways).most_common(30))