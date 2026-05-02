def score_eligibility_query() -> str:
    return """
    SELECT * FROM (
        SELECT
            ws.service_id
            FROM (
                -- 1단계: 사용자 조건으로 적격 서비스만 필터링
                SELECT ws.service_id
                FROM welfare_service ws
                JOIN welfare_target   wt ON ws.service_id = wt.service_id
                JOIN welfare_criteria wc ON ws.service_id = wc.service_id
                WHERE
                    (wc.income_min_pct IS NULL OR wc.income_min_pct <= %s) AND
                    (wc.income_max_pct IS NULL OR wc.income_max_pct >= %s) AND
                    (wt.min_age IS NULL OR wt.min_age <= %s) AND
                    (wt.max_age IS NULL OR wt.max_age >= %s) AND
                    wt.gender IN ('A', %s) AND
                    (wt.sido IS NULL OR wt.sido = '' OR wt.sido ILIKE %s) AND
                    (wt.sigungu IS NULL OR wt.sigungu = '' OR wt.sigungu ILIKE %s) AND
                    (wt.household_types @> ARRAY[%s]::TEXT[] OR wt.household_types IS NULL) AND
                    (wt.employment_statuses IS NULL OR wt.employment_statuses @> ARRAY[%s]::TEXT[]) AND
                    (wt.special_conditions IS NULL OR wt.special_conditions @> ARRAY[%s]::TEXT[])
            ) filtered

            -- 2단계: 필터링된 서비스에 JOIN 후 점수 계산
            JOIN welfare_service  ws using (service_id)
        ) scored
    """