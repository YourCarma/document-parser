class BucketNotFound(Exception):
    """У пользователя нет персонального Document-бакета.

    Живёт здесь, а не в модуле брокера: исключение бросает конвейер перевода,
    и зависимость `translator -> broker` была бы лишней.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"У пользователя '{user_id}' нет Document-бакета")
