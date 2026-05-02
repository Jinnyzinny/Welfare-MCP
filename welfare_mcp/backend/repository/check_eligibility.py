def score_eligibility_query() -> str:
    return """
        SELECT
        ws.service_id,
        ws.service_name,
        ws.service_purpose,
        ws.apply_url
    FROM welfare_service ws
    JOIN welfare_target   wt using (service_id)
    JOIN welfare_criteria wc using (service_id)
    WHERE
        (wc.income_min_pct IS NULL OR %s::integer IS NULL OR wc.income_min_pct <= %s::integer) AND
        (wc.income_max_pct IS NULL OR %s::integer IS NULL OR wc.income_max_pct >= %s::integer) AND
        (wt.min_age IS NULL OR %s::integer IS NULL OR wt.min_age <= %s::integer) AND
        (wt.max_age IS NULL OR %s::integer IS NULL OR wt.max_age >= %s::integer) AND
            (CASE
                WHEN %s::bpchar = 'A' THEN TRUE        -- 사용자가 전체 조회면 무조건 통과
                WHEN wt.gender = 'A' THEN TRUE -- DB가 'A'이면 누구나 통과
                ELSE wt.gender = %s            -- 나머지는 성별 일치해야 통과
            END) AND
        (wt.sido IS NULL OR %s::text IS NULL OR wt.sido = '' OR wt.sido ILIKE %s::text) AND
        (wt.sigungu IS NULL OR %s::text IS NULL OR wt.sigungu = '' OR wt.sigungu ILIKE %s::text) AND
        (wt.household_types IS NULL OR %s::TEXT[] IS NULL OR wt.household_types @> %s::TEXT[]) AND
        (wt.employment_statuses IS NULL OR %s::TEXT[] IS NULL OR wt.employment_statuses @> %s::TEXT[]) AND
        (wt.special_conditions IS NULL OR %s::TEXT[] IS NULL OR wt.special_conditions @> %s::TEXT[])
    """