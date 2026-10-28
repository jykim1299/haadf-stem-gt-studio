import numpy as np
import pytest

from haadf_studio.model import Document


@pytest.fixture
def lattice():
    raw = np.full((180, 240), 1200, np.uint16)
    doc = Document("test-image", raw, np.ones(raw.shape, np.float32))
    for layer, y in [(1, 40), (2, 60), (3, 80)]:
        for x in range(20, 221, 10):
            if layer == 1 and x in (80, 90):
                continue
            p = doc.point(x, y, label="TM", review_status="verified", layer_id=layer)
            doc.points[p.point_id] = p
    return doc
