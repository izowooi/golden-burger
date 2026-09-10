# Golden Apricot 운영

Eco/Fruit는 별도 `data/<runtime>/trades.db`를 사용하며 1분 single-writer Jenkins로 실행한다.
기존 Peach DB를 merge하지 않는다. external workspace와 credential binding은 기존 job 계약을
유지하고 inline secret을 새로 만들지 않는다.
