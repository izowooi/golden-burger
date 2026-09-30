import Link from "next/link";

export default function NotFound() {
  return (
    <>
      <h1>찾을 수 없음</h1>
      <p className="sub">요청한 페이지가 없습니다. <Link href="/">개요로</Link></p>
    </>
  );
}
