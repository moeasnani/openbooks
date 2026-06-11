-- helper: turn 'NULL'/'' sentinels into real NULL
CREATE OR REPLACE MACRO nz(x) AS NULLIF(NULLIF(trim(x), 'NULL'), '');
SET threads TO 16;
SET memory_limit = '96GB';
