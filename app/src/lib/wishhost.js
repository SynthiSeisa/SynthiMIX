// Gedruckter Musikwunsch-Code mit fester Adresse: passt sie zur aktuellen IP?
const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/

/** true, wenn der Code auf eine andere IP zeigt als die, die der Laptop gerade hat.
 *  Bei einem Rechnernamen laesst sich das nicht pruefen → false. */
export function wishHostMismatch(st) {
  if (!st?.running || st?.services?.wishes === false) return false
  const h = st?.wish_host || ''
  return IPV4.test(h) && !!st.ip && h !== st.ip
}
