from datetime import UTC, datetime
from pathlib import Path

from geocatalog.scanner import (
    infer_acquisition_datetime,
    infer_cloud_metadata,
    infer_platform_sensor,
    infer_product,
)


def test_local_processed_landsat8_rgb_product_is_classified():
    path = Path(
        "/data/geomimo/DataProses/BUFFER/101025261/Reflektan_LS8/8Bit/2021/2021_08/"
        "L81150622021114RPIL1TPV20/L8RTP115062m_240421_geo_rgb432_nohaze.tif"
    )

    assert infer_platform_sensor(path) == ("landsat-8", "oli")
    assert infer_product(path) == "rgb432-nohaze"
    assert infer_acquisition_datetime(path) == datetime(2021, 4, 24, tzinfo=UTC)


def test_local_processed_landsat8_date_can_fall_back_to_file_name():
    path = Path("/data/geomimo/L8RTP115062m_240421_geo_rgb432_nohaze.tif")

    assert infer_acquisition_datetime(path) == datetime(2021, 4, 24, tzinfo=UTC)


def test_local_processed_landsat9_rgb_product_is_classified():
    path = Path(
        "/data/geomimo/DataProses/BUFFER/101025261/Reflektan_LS9/16bit/2023/2023_13/"
        "L91190592023204LGNL1TPV20/L9LTP119059m_230723_geo_rgb654.tif"
    )

    assert infer_platform_sensor(path) == ("landsat-9", "oli-2")
    assert infer_product(path) == "rgb654"
    assert infer_acquisition_datetime(path) == datetime(2023, 7, 23, tzinfo=UTC)


def test_landsat_mtl_cloud_metadata_is_extracted(tmp_path):
    tif = tmp_path / "LC08_L1TP_115062_20210424_20210424_02_T1_B4.TIF"
    tif.write_bytes(b"")
    (tmp_path / "LC08_L1TP_115062_20210424_20210424_02_T1_MTL.txt").write_text(
        """
        CLOUD_COVER = 12.34
        CLOUD_COVER_LAND = 5.67
        """,
        encoding="utf-8",
    )

    metadata = infer_cloud_metadata(tif)

    assert metadata["cloud_cover"] == 12.34
    assert metadata["cloud_cover_land"] == 5.67
    assert metadata["cloud_method"] == "landsat_mtl"


def test_sentinel2_cloud_metadata_is_extracted(tmp_path):
    tif = tmp_path / "T47MBU_20260401T030541_B04.jp2"
    tif.write_bytes(b"")
    (tmp_path / "MTD_MSIL2A.xml").write_text(
        "<root><Cloud_Coverage_Assessment>23.5</Cloud_Coverage_Assessment></root>",
        encoding="utf-8",
    )

    metadata = infer_cloud_metadata(tif)

    assert metadata["cloud_cover"] == 23.5
    assert metadata["cloud_method"] == "sentinel2_mtd"
