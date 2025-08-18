from fastapi import UploadFile
from io import BytesIO

class Utils:
    @staticmethod
    def extract_filename(
        file_share_link:str,
        ext: bool    
    ) -> str:
        if ext:
            return file_share_link.split("/")[-1]
        else:
            return file_share_link.split('/')[-1].split('.')[0]
    
    @staticmethod
    def compare_files(files: list[UploadFile]):
        files_form = []
        for file in files:
            file_content = file.file.read()
            files_form.append(
                ("files", (file.filename, file_content, file.content_type))
            )
            file.file.seek(0)
        
        return files_form
    
    @staticmethod
    def get_langs(param):
        match param:
            case "ru":
                langs = ["ru","rs_cyrillic","be","bg","uk","mn","en"]
            case "ar":
                langs = ["ar","fa","ur","ug","en"]
            case None,_:
                langs = ["ru","rs_cyrillic","be","bg","uk","mn","en"]

        return langs 

    @staticmethod
    def build_files(file_bytes:bytes,file_filename:str):
        return [UploadFile(file=BytesIO(file_bytes),filename=file_filename)]
    
utils = Utils()