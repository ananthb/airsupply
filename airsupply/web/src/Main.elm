module Main exposing (main)

{-| The airsupply page.

The whole page: state, view, and the HTTP it takes to keep them current. There
is no JavaScript, and nothing to keep in step with any.

Every request answers with the add-on's entire state rather than a patch of
it, so there is nothing here to merge and nothing that can go stale in one
corner while the rest moves on. Polling takes its pace from that state -- a
second apart while a machine is doing something, four while it is not --
which is a decision that belongs where the state is.

The page's subject is a machine and whose it is. What it leads with is the
handful of things a reading is actually about; everything the machine said is
underneath, for when that is what you want. Setting a machine up happens once,
so it takes up room only while it is unfinished.
-}

import Browser
import Html exposing (..)
import Html.Attributes exposing (..)
import Html.Events exposing (onClick, onInput, onSubmit)
import Http
import Json.Decode as D
import Json.Encode as E
import Time



-- MODEL


type Stage
    = NoAdapter
    | Choose
    | NeedsPairing
    | NeedsPin
    | Ready


type alias Person =
    { id : String, entityId : String, name : String }


type alias Machine =
    { address : String
    , name : String
    , signal : Maybe Int
    , classic : Bool
    , serial : Bool
    , bonded : Bool
    , paired : Bool
    , person : Maybe Person
    , personId : Maybe String
    , missingPerson : Bool
    , lastRead : Maybe String
    }


{-| Something BlueZ can see, which may or may not be a machine of ours yet.
-}
type alias Found =
    { address : String
    , name : String
    , signal : Maybe Int
    , classic : Bool
    , serial : Bool
    , bonded : Bool
    , candidate : Bool
    }


type alias Publishing =
    { configured : Bool, connected : Bool, problem : Maybe String }


type alias Field =
    { label : String, value : String }


type alias Section =
    { title : String, ok : Bool, problem : Maybe String, fields : List Field }


type alias Reading =
    { at : String, sections : List Section }


type alias Question =
    { kind : String, code : Maybe String }


type alias Line =
    { at : String, level : String, text : String }


type alias State =
    { version : String
    , hasAdapter : Bool
    , stage : Stage
    , busy : Maybe String
    , scanning : Bool
    , machines : List Machine
    , machine : Maybe Machine
    , found : List Found
    , people : List Person
    , peopleProblem : Maybe String
    , publishing : Publishing
    , question : Maybe Question
    , problem : Maybe String
    , summary : List Field
    , reading : Maybe Reading
    , log : List Line
    }


{-| What the page knows about the add-on.

Three states, not four. There is no "not asked": `init` fires the first
request, so the page is never sitting on a question it has not put. And a
failure after the first success keeps the state it already had, because a
poll that times out should not blank a reading somebody is reading.
-}
type Link
    = Starting
    | Unreachable Http.Error
    | Live State (Maybe Http.Error)


type alias Model =
    { link : Link
    , pin : String
    , reply : String
    , picking : Bool

    -- One request at a time. A tick that lands while the last one is still
    -- out is dropped rather than queued: the answer is the whole state, so
    -- the one already on its way says everything the second would have.
    , waiting : Bool
    }


init : () -> ( Model, Cmd Msg )
init _ =
    ( { link = Starting, pin = "", reply = "", picking = False, waiting = True }
    , fetch
    )



-- UPDATE


type Msg
    = Tick Time.Posix
    | Answered (Result Http.Error State)
    | Send String (List ( String, E.Value ))
    | PinTyped String
    | ReplyTyped String
    | SendPin
    | SendReply
    | TogglePicking


update : Msg -> Model -> ( Model, Cmd Msg )
update msg model =
    case msg of
        Tick _ ->
            if model.waiting then
                ( model, Cmd.none )

            else
                ( { model | waiting = True }, fetch )

        Answered result ->
            ( absorb result model, Cmd.none )

        Send kind extras ->
            ( { model | waiting = True }, act kind extras )

        PinTyped value ->
            ( { model | pin = digits 8 value }, Cmd.none )

        ReplyTyped value ->
            ( { model | reply = digits 8 value }, Cmd.none )

        SendPin ->
            ( { model | waiting = True }, act "pair" [ ( "pin", E.string model.pin ) ] )

        SendReply ->
            ( { model | waiting = True }
            , act "answer" [ ( "value", E.string model.reply ), ( "accept", E.bool True ) ]
            )

        TogglePicking ->
            ( { model | picking = not model.picking }, Cmd.none )


{-| Keep a typed code to digits. Both things a person types here -- the
machine's PIN and the Bluetooth code -- are numeric, and a stray space is a
failed handshake that looks like a wrong number.
-}
digits : Int -> String -> String
digits limit value =
    value |> String.filter Char.isDigit |> String.left limit


absorb : Result Http.Error State -> Model -> Model
absorb result model =
    case ( result, model.link ) of
        ( Err err, Live state _ ) ->
            -- Something has answered before. Keep it on screen and say only
            -- that the last try did not come back.
            { model | waiting = False, link = Live state (Just err) }

        ( Err err, _ ) ->
            { model | waiting = False, link = Unreachable err }

        ( Ok state, _ ) ->
            { model
                | link = Live state Nothing
                , waiting = False

                -- Clear a field once what it was for is over, so a code does
                -- not sit on screen after it has been accepted.
                , pin =
                    if state.stage == NeedsPin then
                        model.pin

                    else
                        ""
                , reply =
                    if state.question == Nothing then
                        ""

                    else
                        model.reply
                , picking =
                    if state.stage == Choose then
                        False

                    else
                        model.picking
            }


wrong : Http.Error -> String
wrong err =
    case err of
        Http.BadUrl url ->
            url ++ " is not a URL."

        Http.Timeout ->
            "It did not answer in time."

        Http.NetworkError ->
            "Nothing answered; it may have stopped."

        Http.BadStatus code ->
            "It answered " ++ String.fromInt code ++ "."

        Http.BadBody why ->
            -- Nearly always a page older than the add-on serving it, which
            -- is a thing a browser can do by holding an old script. Say so,
            -- because "cannot read" sounds like the add-on is broken.
            "This page looks older than the add-on. Reload it. " ++ why



-- THE ADD-ON


fetch : Cmd Msg
fetch =
    Http.get { url = "api/state", expect = expectState }


act : String -> List ( String, E.Value ) -> Cmd Msg
act kind extras =
    Http.post
        { url = "api/" ++ kind
        , body = Http.jsonBody (E.object extras)
        , expect = expectState
        }


{-| Decode the body whatever the status line says.

A refused action answers 4xx and puts the reason in the state it sends back,
which is the thing the page most needs at that moment -- `Http.expectJson`
would discard it along with the rest of the body and leave only the number.
So the status is kept for the one case where it is all there is: a body that
is not state at all.
-}
expectState : Http.Expect Msg
expectState =
    Http.expectStringResponse Answered <|
        \response ->
            case response of
                Http.BadUrl_ url ->
                    Err (Http.BadUrl url)

                Http.Timeout_ ->
                    Err Http.Timeout

                Http.NetworkError_ ->
                    Err Http.NetworkError

                Http.BadStatus_ metadata body ->
                    decodeState body
                        |> Result.mapError (\_ -> Http.BadStatus metadata.statusCode)

                Http.GoodStatus_ _ body ->
                    decodeState body


decodeState : String -> Result Http.Error State
decodeState body =
    D.decodeString stateDecoder body
        |> Result.mapError (D.errorToString >> Http.BadBody)



-- DECODERS


stateDecoder : D.Decoder State
stateDecoder =
    D.map8 State
        (D.field "version" D.string)
        (D.field "has_adapter" D.bool)
        (D.field "stage" stageDecoder)
        (D.field "busy" (D.nullable D.string))
        (D.field "scanning" D.bool)
        (D.field "machines" (D.list machineDecoder))
        (D.field "machine" (D.nullable machineDecoder))
        (D.field "found" (D.list foundDecoder))
        |> andMap (D.field "people" (D.list personDecoder))
        |> andMap (D.field "people_problem" (D.nullable D.string))
        |> andMap (D.field "publishing" publishingDecoder)
        |> andMap (D.field "question" (D.nullable questionDecoder))
        |> andMap (D.field "problem" (D.nullable D.string))
        |> andMap (D.field "summary" (D.list fieldDecoder))
        |> andMap (D.field "reading" (D.nullable readingDecoder))
        |> andMap (D.field "log" (D.list lineDecoder))


andMap : D.Decoder a -> D.Decoder (a -> b) -> D.Decoder b
andMap =
    D.map2 (|>)


stageDecoder : D.Decoder Stage
stageDecoder =
    D.string
        |> D.map
            (\name ->
                case name of
                    "no-adapter" ->
                        NoAdapter

                    "choose" ->
                        Choose

                    "pairing" ->
                        NeedsPairing

                    "pin" ->
                        NeedsPin

                    _ ->
                        Ready
            )


personDecoder : D.Decoder Person
personDecoder =
    D.map3 Person
        (D.field "id" D.string)
        (D.field "entity_id" D.string)
        (D.field "name" D.string)


machineDecoder : D.Decoder Machine
machineDecoder =
    D.map8 Machine
        (D.field "address" D.string)
        (D.field "name" D.string)
        (D.field "signal" (D.nullable D.int))
        (D.field "classic" D.bool)
        (D.field "serial" D.bool)
        (D.field "bonded" D.bool)
        (D.field "paired" D.bool)
        (D.field "person" (D.nullable personDecoder))
        |> andMap (D.field "person_id" (D.nullable D.string))
        |> andMap (D.field "missing_person" D.bool)
        |> andMap (D.field "last_read" (D.nullable D.string))


foundDecoder : D.Decoder Found
foundDecoder =
    D.map7 Found
        (D.field "address" D.string)
        (D.field "name" D.string)
        (D.field "signal" (D.nullable D.int))
        (D.field "classic" D.bool)
        (D.field "serial" D.bool)
        (D.field "bonded" D.bool)
        (D.field "candidate" D.bool)


publishingDecoder : D.Decoder Publishing
publishingDecoder =
    D.map3 Publishing
        (D.field "configured" D.bool)
        (D.field "connected" D.bool)
        (D.field "problem" (D.nullable D.string))


readingDecoder : D.Decoder Reading
readingDecoder =
    D.map2 Reading
        (D.field "at" D.string)
        (D.field "sections" (D.list sectionDecoder))


sectionDecoder : D.Decoder Section
sectionDecoder =
    D.map4 Section
        (D.field "title" D.string)
        (D.field "ok" D.bool)
        (D.field "problem" (D.nullable D.string))
        (D.field "fields" (D.list fieldDecoder))


fieldDecoder : D.Decoder Field
fieldDecoder =
    D.map2 Field (D.field "label" D.string) (D.field "value" D.string)


questionDecoder : D.Decoder Question
questionDecoder =
    D.map2 Question (D.field "kind" D.string) (D.field "code" (D.nullable D.string))


lineDecoder : D.Decoder Line
lineDecoder =
    D.map3 Line (D.field "at" D.string) (D.field "level" D.string) (D.field "text" D.string)



-- VIEW


view : Model -> Html Msg
view model =
    main_ []
        (case model.link of
            Starting ->
                [ p [ class "muted" ] [ text "Starting." ] ]

            Unreachable err ->
                [ div [ class "banner" ] [ text (notAnswering err) ] ]

            Live state lastTry ->
                page model state lastTry
        )


notAnswering : Http.Error -> String
notAnswering err =
    "The add-on is not answering. " ++ wrong err


page : Model -> State -> Maybe Http.Error -> List (Html Msg)
page model state lastTry =
    List.concat
        [ [ heading state ]
        , maybeView lastTry (\err -> div [ class "banner" ] [ text (notAnswering err) ])
        , maybeView state.problem (\why -> div [ class "banner" ] [ text why ])
        , maybeView state.question (question model state)
        , summary state
        , setup model state
        , machineList model state
        , reading state
        , [ activity state, footer state ]
        ]


maybeView : Maybe a -> (a -> Html Msg) -> List (Html Msg)
maybeView value render =
    case value of
        Just something ->
            [ render something ]

        Nothing ->
            []


heading : State -> Html Msg
heading state =
    div []
        [ div [ class "machine" ]
            (case state.machine of
                Just machine ->
                    [ h1 [] [ text (title machine) ]
                    , span [ class "addr" ] [ text machine.address ]
                    ]

                Nothing ->
                    [ h1 [] [ text "No machine yet" ] ]
            )
        , div [ class "link" ]
            [ span [ class ("dot " ++ lampClass state) ] []
            , text (lampText state)
            ]
        ]


{-| A machine is named for whose it is, once anyone has said.
-}
title : Machine -> String
title machine =
    let
        name =
            if String.isEmpty machine.name then
                machine.address

            else
                machine.name
    in
    case machine.person of
        Just person ->
            name ++ " · " ++ person.name

        Nothing ->
            name


label : Machine -> String
label machine =
    if String.isEmpty machine.name then
        machine.address

    else
        machine.name


lampClass : State -> String
lampClass state =
    if state.busy /= Nothing || state.scanning then
        "work"

    else if state.stage == Ready then
        "live"

    else
        "none"


lampText : State -> String
lampText state =
    case ( state.busy, state.scanning ) of
        ( Just "bond", _ ) ->
            "Pairing with Bluetooth."

        ( Just "pair", _ ) ->
            "Connecting to the machine."

        ( Just "read", _ ) ->
            "Reading."

        ( Just "forget", _ ) ->
            "Forgetting."

        ( Just other, _ ) ->
            other

        ( Nothing, True ) ->
            "Looking for machines."

        ( Nothing, False ) ->
            idleText state


idleText : State -> String
idleText state =
    case state.stage of
        NoAdapter ->
            "Home Assistant has no Bluetooth adapter."

        Choose ->
            "Not set up yet."

        NeedsPairing ->
            "Not paired with Bluetooth."

        NeedsPin ->
            "Paired. Waiting for the machine's PIN."

        Ready ->
            case Maybe.andThen .lastRead state.machine of
                Just at ->
                    "Read at " ++ at ++ "."

                Nothing ->
                    "Set up. Nothing read yet."



-- THE FEW THINGS A READING IS ABOUT


summary : State -> List (Html Msg)
summary state =
    if List.isEmpty state.summary then
        []

    else
        [ div [ class "card figures" ] (List.map figure state.summary) ]


figure : Field -> Html Msg
figure one =
    div [ class "figure" ]
        [ div [ class "figure-label" ] [ text one.label ]
        , div [ class "figure-value" ] [ text one.value ]
        ]



-- SETUP


setup : Model -> State -> List (Html Msg)
setup model state =
    let
        working =
            state.busy /= Nothing
    in
    case state.stage of
        NoAdapter ->
            [ div [ class "card" ]
                [ p [ class "note" ]
                    [ text "Home Assistant cannot see a Bluetooth adapter, so nothing here can work. Check that the host has one and that the Bluetooth integration is running." ]
                ]
            ]

        Choose ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Put the AirMini in pairing mode, then look for it. It is only discoverable while it is." ]
                , div [ class "row-btns" ] [ scanButton state ]
                , foundList state
                ]
            ]

        NeedsPairing ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Put the AirMini in pairing mode and pair within a few seconds of it lighting up. It only answers while it is in pairing mode." ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "bond" []), disabled working ] [ text "Pair" ]
                    , scanButton state
                    ]
                ]
            ]

        NeedsPin ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Type the PIN on the machine's screen. Once only: airsupply keeps the key the machine gives back, and connects with that from then on." ]
                , Html.form [ class "row-btns", onSubmit SendPin ]
                    [ input
                        [ value model.pin
                        , onInput PinTyped
                        , inputmode "numeric"
                        , autocomplete False
                        , attribute "autofocus" ""
                        , placeholder "PIN"
                        , attribute "aria-label" "PIN on the machine's screen"
                        ]
                        []
                    , button [ class "go", disabled (working || String.isEmpty model.pin) ] [ text "Connect" ]
                    ]
                ]
            ]

        Ready ->
            [ div [ class "row-btns", style "margin-bottom" "16px" ]
                [ button [ class "go", onClick (Send "read" []), disabled working ] [ text "Read now" ]
                , button [ class "plain", onClick TogglePicking ]
                    [ text
                        (if model.picking then
                            "Done"

                         else
                            "Machines"
                        )
                    ]
                ]
            ]


scanButton : State -> Html Msg
scanButton state =
    button
        [ onClick (Send "scan" []), disabled (state.scanning || state.busy /= Nothing || not state.hasAdapter) ]
        [ text
            (if state.scanning then
                "Looking"

             else
                "Look for machines"
            )
        ]



-- MACHINES, AND WHOSE THEY ARE


machineList : Model -> State -> List (Html Msg)
machineList model state =
    if not model.picking || state.stage == Choose then
        -- While there is no machine at all, setting one up is the only thing
        -- to do and the setup card is already showing the list.
        []

    else
        [ div [ class "card" ]
            [ h2 [] [ text "Machines" ]
            , table [] (List.map (machineRow state) state.machines)
            , div [ class "row-btns" ]
                [ scanButton state
                , button
                    [ class "plain danger"
                    , onClick (Send "forget" [])
                    , disabled (state.busy /= Nothing || state.machine == Nothing)
                    ]
                    [ text "Forget this machine" ]
                ]
            , foundList state
            , peopleNote state
            ]
        ]


machineRow : State -> Machine -> Html Msg
machineRow state machine =
    let
        chosen =
            Just machine.address == Maybe.map .address state.machine
    in
    tr [ classList [ ( "sel", chosen ) ] ]
        [ td []
            [ div [ class "name" ] [ text (label machine) ]
            , div [ class "sub" ] [ text (machine.address ++ describe machine) ]
            ]
        , td [] [ personPicker state machine ]
        , td [ class "act" ]
            [ if chosen then
                span [ class "sub" ] [ text "shown" ]

              else
                button [ onClick (Send "select" [ ( "address", E.string machine.address ) ]) ] [ text "Show" ]
            ]
        ]


{-| Who the machine belongs to, chosen from Home Assistant's own people.

Stored by their id rather than their name, so renaming a person in Home
Assistant does not orphan a machine.
-}
personPicker : State -> Machine -> Html Msg
personPicker state machine =
    let
        chosen =
            Maybe.withDefault "" machine.personId

        choice person =
            option [ value person.id, selected (person.id == chosen) ] [ text person.name ]

        nobody =
            option [ value "", selected (chosen == "") ] [ text "nobody" ]

        gone =
            if machine.missingPerson then
                [ option [ value chosen, selected True ] [ text "(person deleted)" ] ]

            else
                []
    in
    select
        [ class "person"
        , disabled (List.isEmpty state.people)
        , onInput
            (\id ->
                Send "assign"
                    [ ( "address", E.string machine.address ), ( "person_id", E.string id ) ]
            )
        ]
        (nobody :: gone ++ List.map choice state.people)


peopleNote : State -> Html Msg
peopleNote state =
    case ( state.peopleProblem, List.isEmpty state.people ) of
        ( Just why, _ ) ->
            p [ class "note" ] [ text ("Home Assistant's people are not available: " ++ why) ]

        ( Nothing, True ) ->
            p [ class "note" ] [ text "Home Assistant has no people to assign a machine to yet." ]

        ( Nothing, False ) ->
            text ""


foundList : State -> Html Msg
foundList state =
    let
        known =
            List.map .address state.machines

        candidates =
            List.filter (\d -> d.candidate && not (List.member d.address known)) state.found

        others =
            List.filter (\d -> not d.candidate && not (List.member d.address known)) state.found
    in
    div []
        [ if List.isEmpty candidates then
            p [ class "note", style "margin-top" "12px" ]
                [ text "No new AirMini found. If one is in pairing mode and still does not appear, Home Assistant is out of range." ]

          else
            table [] (List.map foundRow candidates)
        , if List.isEmpty others then
            text ""

          else
            details []
                [ summaryTag (String.fromInt (List.length others) ++ " other Bluetooth devices")
                , table [] (List.map foundRow others)
                ]
        ]


foundRow : Found -> Html Msg
foundRow device =
    tr []
        [ td []
            [ div [ class "name" ] [ text (if String.isEmpty device.name then device.address else device.name) ]
            , div [ class "sub" ] [ text device.address ]
            ]
        , td [ class "sig" ] [ text (signalText device.signal) ]
        , td [ class "act" ]
            [ button [ onClick (Send "select" [ ( "address", E.string device.address ) ]) ] [ text "Add" ] ]
        ]


describe : Machine -> String
describe machine =
    String.concat
        [ if machine.paired then
            ""

          else if machine.bonded then
            "  needs its PIN"

          else
            "  not paired"
        , case machine.signal of
            Just dbm ->
                "  " ++ String.fromInt dbm ++ " dBm"

            Nothing ->
                "  out of range"
        ]


signalText : Maybe Int -> String
signalText signal =
    case signal of
        Just dbm ->
            String.fromInt dbm
                ++ " dBm"
                ++ (if dbm < -80 then
                        " (weak)"

                    else
                        ""
                   )

        Nothing ->
            "not in range"



-- THE PAIRING QUESTION


question : Model -> State -> Question -> Html Msg
question model state ask =
    let
        who =
            Maybe.map label state.machine |> Maybe.withDefault "the machine"

        code =
            Maybe.withDefault "" ask.code

        cancel =
            button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "Cancel" ]
    in
    div [ class "card ask" ]
        (case ask.kind of
            "pincode" ->
                [ p [] [ text ("Bluetooth PIN for " ++ who ++ ". If the machine shows one, type it; otherwise try 0000.") ]
                , Html.form [ class "row-btns", onSubmit SendReply ]
                    [ input [ value model.reply, onInput ReplyTyped, inputmode "numeric", autocomplete False, attribute "autofocus" "", attribute "aria-label" "Bluetooth PIN" ] []
                    , button [ class "go" ] [ text "Send" ]
                    , cancel
                    ]
                ]

            "passkey" ->
                [ p [] [ text ("Type the six-digit code " ++ who ++ " is showing.") ]
                , Html.form [ class "row-btns", onSubmit SendReply ]
                    [ input [ value model.reply, onInput ReplyTyped, inputmode "numeric", autocomplete False, attribute "autofocus" "", attribute "aria-label" "Six-digit code" ] []
                    , button [ class "go" ] [ text "Send" ]
                    , cancel
                    ]
                ]

            "confirm" ->
                [ p [] [ text ("Does " ++ who ++ " show this code?") ]
                , p [ class "code" ] [ text code ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "answer" [ ( "accept", E.bool True ) ]) ] [ text "It matches" ]
                    , button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "It does not" ]
                    ]
                ]

            "authorize" ->
                [ p [] [ text (who ++ " wants to pair.") ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "answer" [ ( "accept", E.bool True ) ]) ] [ text "Allow" ]
                    , button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "Deny" ]
                    ]
                ]

            _ ->
                [ p [] [ text ("Enter this code on " ++ who ++ ".") ]
                , p [ class "code" ] [ text code ]
                ]
        )



-- EVERYTHING THE MACHINE SAID


reading : State -> List (Html Msg)
reading state =
    case state.reading of
        Nothing ->
            []

        Just whole ->
            [ details [ class "card wide" ]
                [ summaryTag ("Everything the machine said, at " ++ whole.at)
                , div [ class "readings" ] (List.map section whole.sections)
                ]
            ]


section : Section -> Html Msg
section content =
    div [ class "section" ]
        [ h2 [] [ text content.title ]
        , case ( content.problem, content.fields ) of
            ( Just why, _ ) ->
                p [ class "failed" ] [ text why ]

            ( Nothing, [] ) ->
                p [ class "muted" ] [ text "Nothing returned." ]

            ( Nothing, fields ) ->
                dl [] (List.map field fields)
        ]


field : Field -> Html Msg
field one =
    div [ class "row" ]
        [ dt [] [ text one.label ]
        , dd [] [ text one.value ]
        ]



-- ACTIVITY


activity : State -> Html Msg
activity state =
    details []
        [ summaryTag "Activity"
        , pre [ class "log" ] (List.map line state.log)
        ]


line : Line -> Html Msg
line one =
    span [ class one.level ] [ text (one.at ++ "  " ++ one.text ++ "\n") ]


footer : State -> Html Msg
footer state =
    p [ class "foot" ]
        [ text ("airsupply " ++ state.version ++ ". Reads only; it never writes to the machine. ")
        , text (publishingText state.publishing)
        ]


publishingText : Publishing -> String
publishingText publishing =
    if not publishing.configured then
        "No MQTT broker, so nothing is published to Home Assistant."

    else if publishing.connected then
        "Publishing to Home Assistant over MQTT."

    else
        case publishing.problem of
            Just why ->
                "MQTT: " ++ why

            Nothing ->
                "Connecting to the MQTT broker."



-- WIRING


summaryTag : String -> Html Msg
summaryTag text_ =
    Html.summary [] [ text text_ ]


inputmode : String -> Attribute msg
inputmode =
    attribute "inputmode"


{-| How often to ask again.

A second apart while a machine is doing something or waiting to be answered,
four while it is idle. The page already knows which, so the pace is read off
the state rather than guessed at from outside it.
-}
pace : Model -> Float
pace model =
    case model.link of
        Live state _ ->
            if state.busy /= Nothing || state.scanning || state.question /= Nothing then
                1000

            else
                4000

        _ ->
            1000


main : Program () Model Msg
main =
    Browser.element
        { init = init
        , update = update
        , view = view
        , subscriptions = \model -> Time.every (pace model) Tick
        }
