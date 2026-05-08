class User:
	def __init__(self, email: str, username: str = '', user_id: str = ''):
		self.email = email
		self.username = username
		self.user_id = user_id

	def detail(self) -> dict:
		return {
			'user_id': self.user_id,
			'email': self.email,
			'username': self.username
		}