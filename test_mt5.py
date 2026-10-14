
import MetaTrader5 as mt5

if not mt5.initialize():
    print("Connection failed:", mt5.last_error())
else:
    account = mt5.account_info()

    if account:
        print("MT5 Connected!")
        print("Account:", account.login)
        print("Server:", account.server)
        print("Balance:", account.balance)
        print("Demo Account:", account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO)
    else:
        print("Account information unavailable:", mt5.last_error())

    mt5.shutdown()
