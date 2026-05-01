def score_eligibility_query() -> str:
    return """
        SELECT
            ws.service_id,
            ws.service_name,
            ws.service_purpose,
            ws.apply_url,

            (1 - (ws.embedding <=> %s::vector))::float AS vector_score,

            (
                (CASE
                    WHEN wt.sido IS NULL OR wt.sido = '' THEN 0.1
                    WHEN wt.sido ILIKE %s               THEN 0.2
                    ELSE 0
                END)
                +
                (CASE
                    WHEN wt.gender = 'A' THEN 0.05
                    WHEN wt.gender = %s  THEN 0.1
                    ELSE 0
                END)
                +
                (CASE
                    WHEN wt.household_types @> ARRAY[%s]::TEXT[] THEN 0.15
                    ELSE 0
                END)
                +
                (CASE
                    WHEN wc.income_min_pct <= %s
                    AND  wc.income_max_pct >= %s THEN 0.15
                    ELSE 0
                END)
            )::float AS profile_bonus

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
        JOIN welfare_target   wt using (service_id)
        JOIN welfare_criteria wc using (service_id)

        ORDER BY (vector_score + intent_bonus + profile_bonus) DESC
        LIMIT 5
    """