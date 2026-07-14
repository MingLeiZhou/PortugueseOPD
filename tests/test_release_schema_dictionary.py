import unittest

from build_release_schema_dictionary import (
    infer_logical_type,
    semantic_inference_issues,
    series_type,
)


class SchemaInferenceTests(unittest.TestCase):
    def test_coordinate_inference_is_token_aware(self):
        self.assertEqual(infer_logical_type("latitude", "number"), "latitude_degrees")
        self.assertEqual(infer_logical_type("geometry_delta_lon", "number"), "longitude_degrees")
        self.assertEqual(infer_logical_type("strong_name_relative_reduction", "number"), "number")
        self.assertEqual(infer_logical_type("$.selected_strategy.isolated", "integer"), "integer")
        self.assertEqual(infer_logical_type("$.selected_strategy.isolated_nodes", "integer"), "integer")

    def test_geometry_inference_is_exact(self):
        self.assertEqual(infer_logical_type("geometry", "string"), "geojson_geometry")
        self.assertEqual(infer_logical_type("original_geometry", "string"), "geojson_geometry")
        self.assertEqual(infer_logical_type("geometry_type", "string"), "string")
        self.assertEqual(infer_logical_type("has_geometry", "boolean"), "boolean")
        self.assertEqual(infer_logical_type("osm_geometry_match_count", "integer"), "integer")
        self.assertEqual(infer_logical_type("mixed_voltage_clusters", "integer"), "integer")

    def test_zero_one_counts_remain_integer(self):
        self.assertEqual(series_type([0, 1], "isolated_nodes"), "integer")
        self.assertEqual(series_type(["0", "1"], "row_count"), "integer")
        self.assertEqual(series_type(["0", "1"], "has_geometry"), "boolean")

    def test_semantic_validator_rejects_known_failure_modes(self):
        records = [
            {"field_name": "relative_reduction", "logical_type": "latitude_degrees"},
            {"field_name": "geometry_count", "logical_type": "geojson_geometry"},
            {"field_name": "row_count", "logical_type": "boolean"},
        ]
        self.assertEqual(len(semantic_inference_issues(records)), 3)


if __name__ == "__main__":
    unittest.main()
