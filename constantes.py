import os

from dotenv import load_dotenv


load_dotenv()

TOKENVOLT = os.environ["TOKENVOLT"]
prefixVolt = os.environ.get("PREFIXVOLT", "T.")
banned_words = ["gypsy", "nigger", "n igger", "n i g g e r", "nigga", "tranny", "trannie", "trannies", "faggot", "faggots", "moskal", "moskals", "m o s k a l", "khokhol", "khokhols", "redskin", "whitey", "chinaman", "chinamen", "pollack", "polack", "hohols", "kokol", "kokols", "kacap", "kacaps", "fag", "retard", "retarded"]

mainAdminId = int(os.environ["MAIN_ADMIN_ID"])
