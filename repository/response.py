class Response:
  def __init__(self, status: int, data, message: str = "Success"):
    self.status = status
    self.data = data
    self.message = message
  
  def json(self) -> dict:
    return {
      "status": {
        "code": self.status,
        "message": self.message
      },
      "data": self.data
    }