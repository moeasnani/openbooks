#!/usr/bin/env python3
# Generates canonical 60-col positional projections for each schema variant.
# Canonical column list: (name, type). type in {str,int,date,dec}
CANON = [
 ("record_number","str"),("fiscal_year","int"),("entity_name","str"),
 ("fund_1_name","str"),("fund_2_name","str"),("fund_3_name","str"),("fund_4_name","str"),
 *[(f"organization_level_{i}_name","str") for i in range(1,11)],
 ("transaction_type","str"),
 *[(f"category_level_{i}_name","str") for i in range(1,8)],
 ("payee_customer_vendor_name","str"),("payee_dba_name","str"),("vendor_id_code","str"),
 ("posting_date","date"),("transaction_description","str"),("transaction_id","str"),
 ("transaction_reference_id","str"),("contract_name","str"),("contract_number","str"),
 ("position_title","str"),("hourly_rate","dec"),("gender","str"),("amount","dec"),
 ("payment_method","str"),("protection_indicator","str"),
 ("fund_1_code","str"),("fund_2_code","str"),("fund_3_code","str"),
 ("appropriation_1_name","str"),("appropriation_1_code","str"),("appropriation_type","str"),
 ("appropriation_category_1_name","str"),("appropriation_category_1_code","str"),
 ("category_level_1_code","str"),("category_level_2_code","str"),("category_level_3_code","str"),
 ("payment__","str"),("fiscal_period","str"),("cabinet","str"),("cabinet_name","str"),
 ("department_code","str"),("bfy","str"),("invoice_number","str"),
 ("subfund_no","str"),("object_no","str"),
]
NAME2I = {n:i for i,(n,_) in enumerate(CANON)}

# For each variant: canonical_name -> physical 0-based position (absent => NULL)
def base38():
    order = [c for c,_ in CANON]
    # base38 physical order: canon 1..27 (skip vendor_id_code) then posting_date..payment_method
    phys = ["record_number","fiscal_year","entity_name","fund_1_name","fund_2_name","fund_3_name","fund_4_name",
            *[f"organization_level_{i}_name" for i in range(1,11)], "transaction_type",
            *[f"category_level_{i}_name" for i in range(1,8)],
            "payee_customer_vendor_name","payee_dba_name",
            "posting_date","transaction_description","transaction_id","transaction_reference_id",
            "contract_name","contract_number","position_title","hourly_rate","gender","amount","payment_method"]
    return {n:i for i,n in enumerate(phys)}

def full57():
    phys = [c for c,_ in CANON][:57]  # canon 1..57 in order
    return {n:i for i,n in enumerate(phys)}

def fy2021():
    phys = [c for c,_ in CANON][:51]  # canon 1..51
    phys += ["invoice_number","payment__","fiscal_period","cabinet","cabinet_name","department_code","bfy"]
    return {n:i for i,n in enumerate(phys)}

def fy2017_18():
    phys = [c for c,_ in CANON][:51]  # canon 1..51
    phys += ["invoice_number","payment__","fiscal_period","cabinet","cabinet_name","department_code","subfund_no","object_no"]
    return {n:i for i,n in enumerate(phys)}

VARIANTS = {"base38":base38(),"full57":full57(),"fy2021":fy2021(),"fy2017_18":fy2017_18()}

def expr(name, typ, pos):
    if pos is None:
        return f"CAST(NULL AS {'INTEGER' if typ=='int' else 'DATE' if typ=='date' else 'DECIMAL(18,4)' if typ=='dec' else 'VARCHAR'}) AS {name}"
    col = f"column{pos:02d}"
    if typ=="int":  return f"TRY_CAST(nz({col}) AS INTEGER) AS {name}"
    if typ=="date": return f"TRY_CAST(nz({col}) AS DATE) AS {name}"
    if typ=="dec":  return f"TRY_CAST(nz({col}) AS DECIMAL(18,4)) AS {name}"
    return f"nz({col}) AS {name}"

def projection(variant, infile):
    m = VARIANTS[variant]
    sel = ",\n  ".join(expr(n,t,m.get(n)) for n,t in CANON)
    return (f"SELECT\n  {sel}\n"
            f"FROM read_csv('{infile}', header=false, skip=1, all_varchar=true)")

if __name__=="__main__":
    import sys
    variant, infile = sys.argv[1], sys.argv[2]
    print(projection(variant, infile))
