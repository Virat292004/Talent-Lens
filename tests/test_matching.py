from shared import platform


def test_skill_normalization_preserves_distinct_technologies():
    assert platform.normalize_skill("React.js") == "react"
    assert platform.normalize_skill("Postgres") == "postgresql"
    assert platform.normalize_skill("PostgreSQL") != platform.normalize_skill("SQL")


def test_skill_extraction_deduplicates_aliases():
    assert platform.extract_skills("React.js, React, PostgreSQL and SQL") == ["postgresql", "react", "sql"]
    assert platform.extract_skills("React.js") == ["react"]


def test_overlapping_employment_ranges_are_unioned():
    profile=platform.extract_profile("Avery Chen\nEXPERIENCE\nSoftware Engineer | 2018 - 2021\nBuilt Python services\nData Engineer | 2020 - 2024\nUsed SQL", "resume-1", "avery.docx", [])
    assert profile["total_experience_years"] == 6
    assert profile["relevant_experience_years"] == 6


def test_unknown_employment_dates_remain_unavailable():
    profile=platform.extract_profile("Avery Chen\nEXPERIENCE\nSoftware Engineer\nBuilt Python services", "resume-1", "avery.docx", [])
    assert profile["total_experience_years"] is None
    assert profile["relevant_experience_years"] is None
    assert any("not reliably detected" in item for item in profile["warnings"])

def test_education_dates_are_not_counted_as_work_experience():
    profile=platform.extract_profile("Avery Chen\nEDUCATION\nBachelor of Software Engineering | 2018 - 2022", "resume-1", "avery.docx", [])
    assert profile["total_experience_years"] is None

def test_total_experience_is_not_substituted_for_unavailable_relevant_experience(monkeypatch):
    monkeypatch.setattr(platform,"semantic_similarity",lambda job,candidate:50.0)
    job={"description":"Backend role","requirements":{"minimum_experience_years":3}}
    profile={"skills":[],"relevant_experience_years":None,"total_experience_years":9,"education":[],"projects":[]}
    assert platform.calculate_match(job,profile)["components"]["experience"] is None


def test_weighted_score_is_deterministic_and_reports_coverage(monkeypatch):
    monkeypatch.setattr(platform,"semantic_similarity",lambda job,candidate:70.0)
    job={"id":"job-1","description":"Python FastAPI role","requirements":{"mandatory_skills":["Python"],"preferred_skills":["Docker"],"minimum_experience_years":4,"summary":"Python FastAPI"}}
    profile={"candidate_id":"candidate-1","resume_id":"resume-1","skills":["Python"],"relevant_experience_years":2,"total_experience_years":2,"education":[],"projects":[],"experience_evidence":["Built Python services"]}
    one=platform.calculate_match(job,profile); two=platform.calculate_match(job,profile)
    assert one==two
    assert one["components"]["skills"]==66.67
    assert one["components"]["experience"]==50.0
    assert one["coverage"]==95.0
    assert "hiring success" in one["warning"]
