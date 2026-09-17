/**
 * Inline stroke icons, one small set shared by the landing actions and the composer.
 *
 * They replace the emoji the UI used before: emoji render differently on every platform (and as
 * tofu on some Windows builds), can't take the brand colour, and look out of place next to text.
 * These inherit `currentColor` and the surrounding font size, so a chip and a button icon always
 * match their label. All are decorative: the label or `aria-label` next to them carries the meaning.
 */
import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  )
}

export function IconBed(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 18v-9" />
      <path d="M3 13h18v5" />
      <path d="M21 18v-3.5a2.5 2.5 0 0 0-2.5-2.5H11V8h7a3 3 0 0 1 3 3" />
      <circle cx="7" cy="10" r="1.75" />
    </Icon>
  )
}

export function IconBreakfast(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 10h13v4a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5v-4Z" />
      <path d="M17 11h1.5a2.5 2.5 0 0 1 0 5H17" />
      <path d="M8 3.5c-.7.9-.7 1.6 0 2.5s.7 1.6 0 2.5" />
      <path d="M12.5 3.5c-.7.9-.7 1.6 0 2.5s.7 1.6 0 2.5" />
    </Icon>
  )
}

export function IconPool(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 16.5c1.6 0 1.6 1.3 3.2 1.3s1.6-1.3 3.2-1.3 1.6 1.3 3.2 1.3 1.6-1.3 3.2-1.3 1.6 1.3 3.2 1.3" />
      <path d="M3 12c1.6 0 1.6 1.3 3.2 1.3S7.8 12 9.4 12s1.6 1.3 3.2 1.3S14.2 12 15.8 12s1.6 1.3 3.2 1.3" />
      <path d="M8.5 12V5.5A2 2 0 0 1 12 4.1" />
      <path d="M15.5 12V5.5A2 2 0 0 0 12 4.1" />
    </Icon>
  )
}

export function IconCalendar(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="5" width="17" height="15" rx="2.5" />
      <path d="M3.5 9.5h17" />
      <path d="M8 3.5V6M16 3.5V6" />
      <path d="M8 13h3" />
    </Icon>
  )
}

export function IconPolicy(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3.5h8.5L19 8v12.5H6Z" />
      <path d="M14 3.5V8h5" />
      <path d="M9 12.5h6M9 16h4" />
    </Icon>
  )
}

export function IconSend(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 12h13" />
      <path d="M12.5 6.5 19 12l-6.5 5.5" />
    </Icon>
  )
}

export function IconBack(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M19 12H6" />
      <path d="M11.5 6.5 6 12l5.5 5.5" />
    </Icon>
  )
}

export function IconChat(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4.5 6.5A2.5 2.5 0 0 1 7 4h10a2.5 2.5 0 0 1 2.5 2.5v7A2.5 2.5 0 0 1 17 16H9.5L5.5 19.5V16H7a2.5 2.5 0 0 1-2.5-2.5Z" />
      <path d="M9 9h6M9 12h4" />
    </Icon>
  )
}
