from concurrent.futures import ThreadPoolExecutor

from transbridge.application.terminology_profiles.service import TerminologyProfileService
from transbridge.persistence.terminology import SqliteTerminologyRepository


def test_read_only_profile_metadata_can_be_read_on_the_background_thread(tmp_path):
    writable = SqliteTerminologyRepository.open(str(tmp_path), "project")
    service = TerminologyProfileService(writable.localization_profiles)
    profile = service.create("project", "方案")
    service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project", "variant", profile.profile_id)
    writable.close()
    reader = SqliteTerminologyRepository.open(str(tmp_path), "project", writable=False)
    try:
        service = TerminologyProfileService(reader.localization_profiles)
        with ThreadPoolExecutor(max_workers=1) as pool:
            summaries = pool.submit(service.list_profile_summaries, "project").result(timeout=3)
            selected = pool.submit(service.selected_revision, "project", "variant").result(timeout=3)
        assert summaries[0].profile_id == selected.profile_id == profile.profile_id
    finally:
        reader.close()
