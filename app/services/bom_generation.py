from typing import List

class BomGenerationService:
    @staticmethod
    def generate_chain(fg_code: str, processes: List[str]):
        # processes is e.g. ["EMB","MET","SLT"]
        # produce reverse chain FG <- SLT <- MET <- EMB <- RM
        chain = []
        # start with FG
        current_parent = fg_code
        # reversed processes
        for proc in reversed(processes):
            child = f"{fg_code}-{proc}"
            chain.append({'parent': current_parent, 'child': child, 'process': proc})
            current_parent = child
        # final raw material
        rm = f"RM-{fg_code}"
        chain.append({'parent': current_parent, 'child': rm, 'process': 'RM'})
        return chain
