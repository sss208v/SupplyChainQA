"""Drop the Milvus collection so we can re-upload with correct security_groups."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from pymilvus import connections, utility, Collection

connections.connect(host="localhost", port="19530")

COLLECTION = "supply_chain_qa_docs"
if utility.has_collection(COLLECTION):
    col = Collection(COLLECTION)
    col.drop()
    print(f"Dropped collection: {COLLECTION}")
else:
    print(f"Collection not found: {COLLECTION}")

connections.disconnect("default")
print("Done.")
